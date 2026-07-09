import logging
from datetime import datetime
from typing import Any, Dict, List

import yt_dlp
from django.utils import timezone

from playlist.models import Video

logger = logging.getLogger("worker")

class YoutubeService:
    """
    A service dedicated to interacting with YouTube to fetch video data.
    Its responsibility is to populate our Video model with the latest
    information from a given playlist.
    """
    def __init__(self, playlist_url: str):
        if not playlist_url:
            raise ValueError("A playlist URL must be provided.")
        self.playlist_url = playlist_url
        self.ydl_opts = {
            'quiet': True,
            'extract_flat': True,  # Don't download, just get metadata
            'force_generic_extractor': True,
        }

    def fetch_playlist_videos(self) -> List[Video]:
        """
        Fetches video entries from the playlist URL and syncs them with the
        database using Django's ORM.

        It uses `update_or_create` to efficiently add new videos or update
        existing ones without creating duplicates.

        Returns:
            A list of all Video objects from the playlist found in the DB.
        """
        logger.info(f"Fetching video details from playlist: {self.playlist_url}")
        
        try:
            with yt_dlp.YoutubeDL(self.ydl_opts) as ydl:
                playlist_info = ydl.extract_info(self.playlist_url, download=False)
        except yt_dlp.utils.DownloadError as e:
            logger.info(f"Error extracting playlist info: {e}")
            return []
        
        if not playlist_info or not isinstance(playlist_info, dict):
            logger.info("No valid playlist information found.")
            return []

        video_entries = playlist_info.get('entries', [])
        video_ids_in_playlist = []
        
        for entry in video_entries:
            if not entry:
                continue

            video_id = entry.get('id')
            video_ids_in_playlist.append(video_id)
            
            # Determine video status
            title = entry.get('title')
            duration = entry.get('duration')
            
            if title == '[Private Video]':
                status = Video.VideoStatus.PRIVATE
            elif duration is None or title == '[Deleted video]':
                status = Video.VideoStatus.UNAVAILABLE
            else:
                status = Video.VideoStatus.AVAILABLE

            # Use Django's ORM to create or update the video record.
            # This replaces the need to manually check if a video exists.
            video, created = Video.objects.update_or_create(
                id=video_id,
                defaults={
                    'title': title or "Unknown Title",
                    'uploader': entry.get('uploader'),
                    'duration': duration,
                    'url': f"https://www.youtube.com/watch?v={video_id}",
                    'status': status,
                    'last_check_at': timezone.now()
                }
            )
            
            if created:
                logger.info(f"  [NEW] Found video: {video.title}")
            else:
                logger.debug(f"  [UPDATE] Checked video: {video.title}")
        
        logger.info(f"Finished fetching. Found {len(video_entries)} videos in playlist.")
        
        # Return all videos from the playlist that are now in our database
        return list(Video.objects.filter(id__in=video_ids_in_playlist))