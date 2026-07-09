import asyncio
import logging
import time

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db.models import Q
from django.utils import timezone

from playlist.models import LocalTrack, Video
from playlist.services.downloader_service import DownloaderService
from playlist.services.metadata_parser_service import \
    MetadataParserService  # Ensure TaggerService can import it
from playlist.services.tagger_service import TaggerService
from playlist.services.youtube_service import YoutubeService

logger = logging.getLogger("worker")

class Command(BaseCommand):
    help = 'Performs a full sync of the YouTube playlist: fetches video list, downloads new audio, and tags it.'

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS("--- Starting YouTube Playlist Sync ---"))

        # --- Step 1: Sync playlist info from YouTube ---
        self.stdout.write("[1/3] Fetching latest playlist details from YouTube...")
        try:
            yt_service = YoutubeService(playlist_url=settings.PLAYLIST_URL)
            yt_service.fetch_playlist_videos()
            self.stdout.write(self.style.SUCCESS("YouTube playlist sync complete."))
        except Exception as e:
            self.stderr.write(self.style.ERROR(f"Error fetching playlist details: {e}"))
            # We can decide to exit here if this step fails
            return

        # --- Step 2: Identify videos that need processing ---
        self.stdout.write("\n[2/3] Identifying tracks to download or retry...")
        
        now = timezone.now()
        
        # A video needs processing if it's available on YouTube AND either:
        # a) It has no local track entry at all.
        # b) Its local track failed and the retry time has been reached.
        videos_to_process = Video.objects.filter(
            Q(status=Video.VideoStatus.AVAILABLE) &
            (
                Q(local_track__isnull=True) |
                (Q(local_track__processing_status=LocalTrack.ProcessingStatus.FAILED) & Q(local_track__retry_at__lte=now))
            )
        ).distinct()

        if not videos_to_process.exists():
            self.stdout.write(self.style.SUCCESS("No new or failed videos to process. System is up to date."))
            self.stdout.write(self.style.SUCCESS("--- Sync Complete ---"))
            return

        self.stdout.write(self.style.WARNING(f"Found {videos_to_process.count()} videos to process."))

        # --- Step 3: Download and Tag each video ---
        self.stdout.write("\n[3/3] Processing tracks...")
        for video in videos_to_process:
            self.stdout.write(f"\n--- Processing: {video.title} ---")
            try:
                # Part A: Download
                downloader = DownloaderService(video=video)
                track = downloader.download_audio()

                # Part B: Tag (only if download was successful)
                if track.processing_status == LocalTrack.ProcessingStatus.DOWNLOADED:
                    self.stdout.write("Download successful. Proceeding to tagging...")
                    tagger = TaggerService(track=track)
                    asyncio.run(tagger.tag_and_rename_track())
                elif track.processing_status == LocalTrack.ProcessingStatus.FAILED:
                     self.stderr.write(self.style.ERROR(f"Download failed for '{video.title}'."))
                
                # Be a good citizen and don't spam the servers
                self.stdout.write("Cooling down for 5 seconds...")
                time.sleep(5)

            except Exception as e:
                self.stderr.write(self.style.ERROR(f"An unexpected error occurred while processing '{video.title}': {e}"))
        
        self.stdout.write(self.style.SUCCESS("\n--- YouTube Playlist Sync Finished ---"))