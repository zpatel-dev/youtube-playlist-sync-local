import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "music_manager.settings")
import django

django.setup()
import asyncio
import logging

from playlist.models import LocalTrack
from playlist.services.tagger_service import TaggerService

logger = logging.getLogger("django")

# Find a track that is 'DOWNLOADED' and ready for tagging
target_track = LocalTrack.objects.filter(
    processing_status__in=[
        LocalTrack.ProcessingStatus.DOWNLOADED,
        LocalTrack.ProcessingStatus.FAILED
    ]
).first()

if target_track:
    logger.info(f"Found track to tag: {target_track}")
    logger.info(f"Original file path: {target_track.local_path}")

    # Initialize the TaggerService
    service = TaggerService(track=target_track)

    # Run the async method using asyncio.run()
    result_track = asyncio.run(service.tag_and_rename_track())

    # Check the result
    logger.info(f"Service finished. Final status: {result_track.processing_status}")
    logger.info(f"New file path: {result_track.local_path}")
else:
    logger.info("No downloaded tracks found to tag. Run the DownloaderService first.")
