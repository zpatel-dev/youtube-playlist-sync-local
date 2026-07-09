"""Live smoke test: resync the REAL playlist via yt-dlp (no download, metadata only).

Skipped unless RUN_LIVE=1, so the default suite stays offline and deterministic.
Uses extract_flat, so it only hits YouTube for the playlist listing — fast and light.
"""
import os
import unittest

from django.conf import settings
from django.test import TestCase

from playlist.models import Video
from playlist.services.youtube_service import YoutubeService


@unittest.skipUnless(os.getenv("RUN_LIVE") == "1", "live network test (set RUN_LIVE=1 to enable)")
class LivePlaylistSmokeTest(TestCase):
    def test_real_playlist_resync(self):
        videos = YoutubeService(playlist_url=settings.PLAYLIST_URL).fetch_playlist_videos()
        if not videos:
            self.skipTest("yt-dlp returned no videos (bot-blocked, empty, or offline).")
        self.assertGreater(Video.objects.count(), 0)
        print(f"\nLIVE: fetched {len(videos)} video(s) from the real playlist.")
