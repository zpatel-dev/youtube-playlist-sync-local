import logging
import os
from datetime import timedelta

import yt_dlp
from django.conf import settings
from django.utils import timezone

from playlist.models import LocalTrack, Video
from playlist.utils.file_utils import calculate_md5

logger = logging.getLogger("worker")
class DownloaderService:
    """
    A service dedicated to downloading a YouTube video as an MP3 file.
    Its responsibility is to handle the download process for a single video
    and update the state of its corresponding LocalTrack model.
    """
    def __init__(self, video: Video):
        self.video = video
        self.output_path = settings.OUTPUT_DIRECTORY
        os.makedirs(self.output_path, exist_ok=True)

    def download_audio(self) -> LocalTrack:
        """
        Downloads the audio for the service's video object.
        - Gets or creates a LocalTrack instance.
        - Sets the status to DOWNLOADING.
        - Attempts to download using yt-dlp.
        - On success, updates status to DOWNLOADED, stores file path, and MD5 hash.
        - On failure, updates status to FAILED and sets a retry time.
        """
        track, _ = LocalTrack.objects.get_or_create(video=self.video)
        
        # Prevent re-downloading if already completed
        if track.processing_status == LocalTrack.ProcessingStatus.COMPLETED and os.path.exists(track.local_path):
            logger.info(f"[SKIP] '{self.video.title}' is already downloaded and processed.")
            return track

        track.processing_status = LocalTrack.ProcessingStatus.DOWNLOADING
        track.save()

        # Temporary filename template, using the robust video ID
        temp_filename_template = os.path.join(self.output_path, f"{self.video.id}.%(ext)s")

        ydl_opts = {
            'format': 'bestaudio/best',
            'outtmpl': temp_filename_template,
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }],
            'quiet': False,
        }

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                # Using extract_info is a cleaner way to control the download
                ydl.extract_info(self.video.url, download=True)

            final_path = os.path.join(self.output_path, f"{self.video.id}.mp3")

            if not os.path.exists(final_path):
                 raise FileNotFoundError(f"Expected output file not found at {final_path}")

            # --- SUCCESS ---
            track.local_path = final_path
            track.md5_hash = calculate_md5(final_path) # <-- Calculate and set the hash
            track.processing_status = LocalTrack.ProcessingStatus.DOWNLOADED
            track.downloaded_at = timezone.now()
            track.fail_count = 0 
            track.retry_at = None
            logger.info(f"[SUCCESS] Downloaded '{self.video.title}' to '{final_path}'")
            logger.info(f"          MD5 Hash: {track.md5_hash}")

        except Exception as e:
            # --- FAILURE ---
            track.processing_status = LocalTrack.ProcessingStatus.FAILED
            track.fail_count += 1
            retry_delay = timedelta(days=track.fail_count * 2)
            track.retry_at = timezone.now() + retry_delay
            logger.exception(f"[FAILURE] Error downloading '{self.video.title}': {e}")
            logger.warning(f"           Will retry after {track.retry_at.strftime('%Y-%m-%d %H:%M')}") # type: ignore
        
        track.save()
        return track