# type: ignore
import asyncio
import logging
import os
from typing import Optional
from urllib.request import urlopen

import eyed3
from asgiref.sync import sync_to_async
from django.utils import timezone
from eyed3.id3.frames import ImageFrame
from shazamio import Shazam

from playlist.models import LocalTrack
from playlist.services.metadata_parser_service import MetadataParserService
from playlist.utils.dict_utils import find_deepest_metadata_key
from playlist.utils.file_utils import sanitize_string

logger = logging.getLogger("worker")


class TaggerService:
    """
    A service for recognizing, tagging, and renaming a downloaded audio track.
    It uses Shazam for recognition and eyed3 for writing ID3 tags.
    """

    def __init__(self, track: LocalTrack):
        self.track = track
        self.shazam = Shazam()
        self.file_path = track.local_path

    # --- Create async-safe database methods ---
    @sync_to_async
    def _save_track(self):
        """An async-safe method to save the track instance."""
        self.track.save()

    @sync_to_async
    def _mark_as_failed(self, error_message: str):
        """An async-safe helper to update track status on failure."""
        self.track.processing_status = LocalTrack.ProcessingStatus.TAGGING
        self.track.fail_count += 1
        self.track.save()

    async def tag_and_rename_track(self) -> LocalTrack:
        """
        Orchestrates the entire tagging and renaming process, now with an AI-powered fallback.
        """
        if not self.file_path or not os.path.exists(self.file_path):
            title = await sync_to_async(lambda: self.track.video.title)()
            logger.info(f"File not found for track: {title}")
            await self._mark_as_failed("File path does not exist.")
            return self.track

        self.track.processing_status = LocalTrack.ProcessingStatus.TAGGING
        await self._save_track()
        title = await sync_to_async(lambda: self.track.video.title)()
        logger.info(f"Tagging '{title}'...")

        tags_data = None
        try:
            logger.info("Attempting recognition with Shazam...")
            tags_data = await self._recognize_song()

            # --- Fallback Method: Generative AI ---
            if not tags_data:
                logger.info("Shazam failed. Attempting fallback using video title metadata.")
                parser = MetadataParserService()
                # Run the synchronous AI call in a thread to avoid blocking
                ai_metadata = await sync_to_async(parser.extract_metadata_from_title)(
                    await sync_to_async(lambda: self.track.video)()
                )

                if ai_metadata:
                    # Structure the data to match what the rest of the service expects
                    tags_data = {
                        "title": ai_metadata.get("title"),
                        "artist": ai_metadata.get("artist"),
                        "album": ai_metadata.get("title"),  # Use title as a fallback album
                        "label": None,
                        "release_year": None,
                        "cover_art_url": None,  # AI can't find cover art
                    }

            if not tags_data:
                raise ValueError("Could not recognize song with Shazam or parse from title.")

            # --- Process with the data we found ---
            await sync_to_async(self._update_mp3_tags)(tags_data)
            await sync_to_async(self._update_mp3_cover_art)(tags_data.get("cover_art_url"))
            new_file_path = await sync_to_async(self._rename_file)(tags_data)

            self.track.local_path = new_file_path
            self.track.processing_status = LocalTrack.ProcessingStatus.COMPLETED
            self.track.fail_count = 0
            self.track.retry_at = None
            logger.info(f"[SUCCESS] Tagged and renamed to '{os.path.basename(new_file_path)}'")

        except Exception as e:
            logger.exception(f"Error tagging '{title}': {e}")
            await self._mark_as_failed(str(e))

        await self._save_track()
        return self.track

    async def _recognize_song(self, attempts: int = 3) -> Optional[dict]:
        """Recognize song using Shazam, with retries."""
        for attempt in range(attempts):
            try:
                out = await self.shazam.recognize(self.file_path)
                if out and out.get("track"):
                    track_info = out["track"]
                    return {
                        "title": track_info.get("title", "Unknown Title"),
                        "artist": track_info.get("subtitle", "Unknown Artist"),
                        "album": find_deepest_metadata_key(track_info, "Album") or "Unknown Album",
                        "label": find_deepest_metadata_key(track_info, "Label"),
                        "release_year": find_deepest_metadata_key(track_info, "Released"),
                        "cover_art_url": track_info.get("images", {}).get("coverart"),
                    }
            except Exception as e:
                logger.exception(f"Shazam recognition attempt {attempt + 1}/{attempts} failed: {e}")
                await asyncio.sleep(5)
        return None

    def _update_mp3_tags(self, tags: dict):
        """Write text-based metadata to the MP3 file."""
        audiofile = eyed3.load(self.file_path)
        if not audiofile or audiofile.tag is None:
            audiofile.initTag()

        audiofile.tag.title = tags.get("title")
        audiofile.tag.artist = tags.get("artist")
        audiofile.tag.album = tags.get("album")
        audiofile.tag.publisher = tags.get("label") or ""
        audiofile.tag.release_date = tags.get("release_year") or ""
        audiofile.tag.save()
        logger.info("  Updated MP3 tags.")

    def _update_mp3_cover_art(self, cover_url: Optional[str]):
        """Download and write cover art to the MP3 file."""
        if not cover_url:
            logger.info("  No cover art URL found.")
            return

        audiofile = eyed3.load(self.file_path)
        if not audiofile or audiofile.tag is None:
            audiofile.initTag()

        try:
            img_data = urlopen(cover_url).read()
            audiofile.tag.images.set(ImageFrame.FRONT_COVER, img_data, "image/jpeg")
            audiofile.tag.save()
            logger.info("Updated cover art.")
        except Exception as e:
            logger.exception(f"  Could not download or set cover art: {e}")

    def _rename_file(self, tags: dict) -> str:
        """Constructs a new filename and renames the file, handling duplicates."""
        directory = os.path.dirname(self.file_path)
        title = sanitize_string(tags.get("title", "Unknown Title"))
        artist = sanitize_string(tags.get("artist", "Unknown Artist"))

        base_filename = f"{artist} - {title}.mp3"
        new_path = os.path.join(directory, base_filename)

        counter = 1
        while os.path.exists(new_path) and new_path != self.file_path:
            filename, extension = os.path.splitext(base_filename)
            new_filename = f"{filename} ({counter}){extension}"
            new_path = os.path.join(directory, new_filename)
            counter += 1

        os.rename(self.file_path, new_path)
        logger.info(f"  Renamed file to '{os.path.basename(new_path)}'.")
        return new_path
