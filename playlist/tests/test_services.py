"""Service layer with every external boundary mocked (yt-dlp, Shazam, Gemini, eyed3).

These exercise the real service code paths — status transitions, the Shazam→Gemini
fallback, file rename — without touching the network or needing real media files.
"""
import asyncio
import os
import tempfile
from unittest import mock

import yt_dlp
from django.test import TestCase, override_settings

from playlist.models import LocalTrack, Video
from playlist.services.downloader_service import DownloaderService
from playlist.services.metadata_parser_service import MetadataParserService
from playlist.services.tagger_service import TaggerService
from playlist.services.youtube_service import YoutubeService


def make_video(vid="vid00000001", title="Some Song", duration=200):
    return Video.objects.create(
        id=vid, title=title, url=f"https://youtu.be/{vid}", duration=duration
    )


class YoutubeServiceTests(TestCase):
    FAKE_PLAYLIST = {
        "entries": [
            {"id": "vidavailable", "title": "Available Song", "uploader": "Artist", "duration": 210},
            {"id": "vidprivate0", "title": "[Private Video]", "uploader": None, "duration": None},
            {"id": "vidunavail0", "title": "Some Deleted", "uploader": None, "duration": None},
            None,  # yt-dlp sometimes yields None entries; must be skipped
        ]
    }

    def test_fetch_creates_videos_with_correct_status(self):
        with mock.patch("playlist.services.youtube_service.yt_dlp.YoutubeDL") as MockYDL:
            MockYDL.return_value.__enter__.return_value.extract_info.return_value = self.FAKE_PLAYLIST
            result = YoutubeService("https://youtube.com/playlist?list=x").fetch_playlist_videos()

        self.assertEqual(Video.objects.count(), 3)
        self.assertEqual(len(result), 3)
        self.assertEqual(Video.objects.get(id="vidavailable").status, Video.VideoStatus.AVAILABLE)
        self.assertEqual(Video.objects.get(id="vidprivate0").status, Video.VideoStatus.PRIVATE)
        self.assertEqual(Video.objects.get(id="vidunavail0").status, Video.VideoStatus.UNAVAILABLE)

    def test_fetch_is_idempotent(self):
        with mock.patch("playlist.services.youtube_service.yt_dlp.YoutubeDL") as MockYDL:
            MockYDL.return_value.__enter__.return_value.extract_info.return_value = self.FAKE_PLAYLIST
            svc = YoutubeService("https://youtube.com/playlist?list=x")
            svc.fetch_playlist_videos()
            svc.fetch_playlist_videos()
        self.assertEqual(Video.objects.count(), 3)  # update_or_create, not duplicate

    def test_download_error_returns_empty_list(self):
        with mock.patch("playlist.services.youtube_service.yt_dlp.YoutubeDL") as MockYDL:
            MockYDL.return_value.__enter__.return_value.extract_info.side_effect = (
                yt_dlp.utils.DownloadError("blocked")
            )
            result = YoutubeService("https://youtube.com/playlist?list=x").fetch_playlist_videos()
        self.assertEqual(result, [])
        self.assertEqual(Video.objects.count(), 0)

    def test_empty_url_raises(self):
        with self.assertRaises(ValueError):
            YoutubeService("")


class DownloaderServiceTests(TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        self.video = make_video()

    def test_successful_download_sets_downloaded_status_and_hash(self):
        with override_settings(OUTPUT_DIRECTORY=self.tmp):
            with mock.patch("playlist.services.downloader_service.yt_dlp.YoutubeDL") as MockYDL:
                def fake_extract(url, download):
                    with open(os.path.join(self.tmp, f"{self.video.id}.mp3"), "wb") as f:
                        f.write(b"fake audio")
                    return {}
                MockYDL.return_value.__enter__.return_value.extract_info.side_effect = fake_extract
                track = DownloaderService(self.video).download_audio()

        self.assertEqual(track.processing_status, LocalTrack.ProcessingStatus.DOWNLOADED)
        self.assertTrue(track.md5_hash)
        self.assertTrue(track.local_path.endswith(f"{self.video.id}.mp3"))
        self.assertEqual(track.fail_count, 0)

    def test_failed_download_sets_failed_status_and_schedules_retry(self):
        with override_settings(OUTPUT_DIRECTORY=self.tmp):
            with mock.patch("playlist.services.downloader_service.yt_dlp.YoutubeDL") as MockYDL:
                MockYDL.return_value.__enter__.return_value.extract_info.side_effect = RuntimeError("boom")
                track = DownloaderService(self.video).download_audio()

        self.assertEqual(track.processing_status, LocalTrack.ProcessingStatus.FAILED)
        self.assertEqual(track.fail_count, 1)
        self.assertIsNotNone(track.retry_at)

    def test_already_completed_download_is_skipped(self):
        existing = os.path.join(self.tmp, f"{self.video.id}.mp3")
        with open(existing, "wb") as f:
            f.write(b"already here")
        LocalTrack.objects.create(
            video=self.video,
            local_path=existing,
            processing_status=LocalTrack.ProcessingStatus.COMPLETED,
        )
        with override_settings(OUTPUT_DIRECTORY=self.tmp):
            with mock.patch("playlist.services.downloader_service.yt_dlp.YoutubeDL") as MockYDL:
                track = DownloaderService(self.video).download_audio()
                MockYDL.assert_not_called()
        self.assertEqual(track.processing_status, LocalTrack.ProcessingStatus.COMPLETED)


class TaggerServiceTests(TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        self.video = make_video()
        self.path = os.path.join(self.tmp, f"{self.video.id}.mp3")
        with open(self.path, "wb") as f:
            f.write(b"fake mp3")
        self.track = LocalTrack.objects.create(
            video=self.video,
            local_path=self.path,
            processing_status=LocalTrack.ProcessingStatus.DOWNLOADED,
        )
        _ = self.track.video  # cache the FK so async helpers never hit the DB in a worker thread

    def _run(self, service):
        return asyncio.run(service.tag_and_rename_track())

    def test_shazam_success_tags_and_renames_to_completed(self):
        payload = {"track": {"title": "Real Title", "subtitle": "Real Artist", "images": {}}}
        with mock.patch("playlist.services.tagger_service.Shazam") as MockShazam, \
             mock.patch("playlist.services.tagger_service.eyed3.load", return_value=mock.MagicMock()):
            MockShazam.return_value.recognize = mock.AsyncMock(return_value=payload)
            service = TaggerService(track=self.track)
            service._save_track = mock.AsyncMock()  # avoid cross-thread ORM writes
            self._run(service)

        expected = os.path.join(self.tmp, "Real Artist - Real Title.mp3")
        self.assertEqual(service.track.processing_status, LocalTrack.ProcessingStatus.COMPLETED)
        self.assertTrue(os.path.exists(expected))
        self.assertFalse(os.path.exists(self.path))
        self.assertEqual(service.track.local_path, expected)

    def test_falls_back_to_gemini_when_shazam_fails(self):
        ai = {"title": "AI Title", "artist": "AI Artist", "album": "AI Album",
              "label": None, "release_year": None, "cover_art_url": None}
        with mock.patch("playlist.services.tagger_service.Shazam"), \
             mock.patch("playlist.services.tagger_service.eyed3.load", return_value=mock.MagicMock()), \
             mock.patch("playlist.services.tagger_service.MetadataParserService") as MockParser:
            MockParser.return_value.extract_metadata_from_title.return_value = ai
            service = TaggerService(track=self.track)
            service._recognize_song = mock.AsyncMock(return_value=None)  # Shazam finds nothing
            service._save_track = mock.AsyncMock()
            self._run(service)

        self.assertEqual(service.track.processing_status, LocalTrack.ProcessingStatus.COMPLETED)
        self.assertTrue(os.path.exists(os.path.join(self.tmp, "Ai Artist - Ai Title.mp3")))

    def test_marks_failed_when_both_shazam_and_gemini_fail(self):
        with mock.patch("playlist.services.tagger_service.Shazam"), \
             mock.patch("playlist.services.tagger_service.MetadataParserService") as MockParser:
            MockParser.return_value.extract_metadata_from_title.return_value = None
            service = TaggerService(track=self.track)
            service._recognize_song = mock.AsyncMock(return_value=None)
            service._save_track = mock.AsyncMock()
            service._mark_as_failed = mock.AsyncMock()
            self._run(service)

        service._mark_as_failed.assert_awaited_once()
        self.assertNotEqual(service.track.processing_status, LocalTrack.ProcessingStatus.COMPLETED)

    def test_missing_file_marks_failed_without_touching_shazam(self):
        self.track.local_path = "/no/such/file.mp3"
        service = TaggerService(track=self.track)
        service._mark_as_failed = mock.AsyncMock()
        self._run(service)
        service._mark_as_failed.assert_awaited_once()


class MetadataParserServiceTests(TestCase):
    def _mock_response(self, parsed):
        resp = mock.MagicMock()
        resp.parsed = parsed
        return resp

    def test_parses_ai_metadata(self):
        parsed = {"title": "T", "artist": "A", "album": "Al", "label": None, "release_year": None}
        with mock.patch("playlist.services.metadata_parser_service.client") as client:
            client.models.generate_content.return_value = self._mock_response(parsed)
            video = make_video(duration=200)
            result = MetadataParserService().extract_metadata_from_title(video)
        self.assertEqual(result["artist"], "A")
        self.assertEqual(result["title"], "T")

    def test_long_video_uses_title_only_prompt(self):
        parsed = {"title": "T", "artist": "A", "album": "Al", "label": None, "release_year": None}
        with mock.patch("playlist.services.metadata_parser_service.client") as client:
            client.models.generate_content.return_value = self._mock_response(parsed)
            video = make_video(vid="vid00000009", duration=4000)  # >3000 => title-only branch
            result = MetadataParserService().extract_metadata_from_title(video)
        self.assertIsNotNone(result)
        client.models.generate_content.assert_called_once()

    def test_returns_none_for_empty_title(self):
        video = Video(id="vid00000010", title="", url="https://youtu.be/vid00000010", duration=100)
        self.assertIsNone(MetadataParserService().extract_metadata_from_title(video))
