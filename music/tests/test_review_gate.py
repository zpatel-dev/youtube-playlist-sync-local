"""The review gate: a playlist entry that is not an art track is not downloaded.

Offline throughout. `music.ingest.youtube._ydl` is the single yt-dlp seam, and
`download_audio` is patched separately so a test can assert it was never
reached — which is the whole point of the feature.
"""
from unittest import mock

from django.test import TestCase
from django.urls import reverse

from music.ingest import youtube
from music.jobs import engine
from music.jobs.handlers import youtube as handlers
from music.models import Availability, Job, JobState, YoutubeVideo

ART = {"id": "art00000001", "title": "Kala Chashma", "track": "Kala Chashma",
       "artist": "Amar Arshi, Badshah", "album": "Baar Baar Dekho",
       "uploader": "Amar Arshi - Topic"}
VIDEO = {"id": "vid00000001",
         "title": "Coke Studio Bharat | Khalasi | Aditya Gadhvi x Achint",
         "uploader": "Coke Studio India"}


class FakeYDL:
    def __init__(self, info):
        self._info = info

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def extract_info(self, url, download=False):
        return self._info


def _video(video_id="vid00000001", **kw):
    kw.setdefault("availability", Availability.AVAILABLE)
    kw.setdefault("title", "Some Entry")
    return YoutubeVideo.objects.create(
        video_id=video_id,
        url=f"https://www.youtube.com/watch?v={video_id}", **kw)


def _run_download(video_id, approved=False):
    payload = {"video_id": video_id}
    if approved:
        payload["approved"] = True
    job = engine.enqueue("youtube.download", payload,
                         dedup_key=f"youtube.download:{video_id}")
    return handlers.download(job)


class GateTests(TestCase):
    def setUp(self):
        self.downloads = mock.patch.object(youtube, "download_audio").start()
        self.addCleanup(mock.patch.stopall)

    def test_a_video_upload_is_held_and_not_downloaded(self):
        video = _video()
        with mock.patch.object(youtube, "_ydl", side_effect=lambda o: FakeYDL(VIDEO)):
            result = _run_download(video.pk)
        video.refresh_from_db()
        self.assertIn("held", result)
        self.assertEqual(video.availability, Availability.NEEDS_REVIEW)
        self.assertTrue(video.hold_reason)
        self.downloads.assert_not_called()
        self.assertIsNone(video.track_id)

    def test_holding_is_not_a_failure(self):
        """No retry_at and no fail_count: it waits on a person, not a timer."""
        video = _video()
        with mock.patch.object(youtube, "_ydl", side_effect=lambda o: FakeYDL(VIDEO)):
            _run_download(video.pk)
        video.refresh_from_db()
        self.assertIsNone(video.retry_at)
        self.assertEqual(video.fail_count, 0)
        self.assertEqual(video.last_error, "")

    def test_an_art_track_downloads(self):
        video = _video(video_id="art00000001")
        self.downloads.return_value = self._staged_file()
        with mock.patch.object(youtube, "_ydl", side_effect=lambda o: FakeYDL(ART)):
            result = _run_download(video.pk)
        video.refresh_from_db()
        self.assertNotIn("held", result)
        self.assertEqual(video.availability, Availability.AVAILABLE)
        self.downloads.assert_called_once()

    def test_an_approved_download_skips_the_check_entirely(self):
        video = _video()
        self.downloads.return_value = self._staged_file()
        # _ydl raising proves the probe was never attempted.
        with mock.patch.object(youtube, "_ydl", side_effect=AssertionError("probed")):
            _run_download(video.pk, approved=True)
        self.downloads.assert_called_once()

    def test_a_probe_failure_falls_through_to_downloading(self):
        """A network hiccup must not look like a verdict."""
        video = _video()
        self.downloads.return_value = self._staged_file()
        with mock.patch.object(youtube, "_ydl", side_effect=RuntimeError("no network")):
            _run_download(video.pk)
        video.refresh_from_db()
        self.assertEqual(video.availability, Availability.AVAILABLE)
        self.downloads.assert_called_once()

    def _staged_file(self):
        from django.conf import settings
        from pathlib import Path
        staging = Path(settings.DOWNLOAD_STAGING)
        staging.mkdir(parents=True, exist_ok=True)
        path = staging / "gate-fixture.mp3"
        path.write_bytes(b"\0" * 2048)
        self.addCleanup(lambda: path.unlink(missing_ok=True))
        return path


class QueueTests(TestCase):
    """Held entries are skipped by the availability filter both queries already
    had, which is why neither query needed changing."""

    def test_queue_skips_held_entries(self):
        _video(video_id="held0000001", availability=Availability.NEEDS_REVIEW)
        _video(video_id="open0000001")
        self.assertEqual(handlers._queue_downloads(), 1)
        payloads = [j.payload.get("video_id") for j in Job.objects.all()]
        self.assertEqual(payloads, ["open0000001"])

    def test_pending_downloads_skips_held_entries(self):
        _video(video_id="held0000002", availability=Availability.NEEDS_REVIEW)
        _video(video_id="open0000002")
        ids = [v.video_id for v in youtube.pending_downloads()]
        self.assertEqual(ids, ["open0000002"])


class ApproveViewTests(TestCase):
    def test_approving_flags_the_row_and_queues_a_download(self):
        video = _video(availability=Availability.NEEDS_REVIEW,
                       hold_reason="not an art track")
        response = self.client.post(
            reverse("action_approve_video", args=[video.pk]))
        self.assertEqual(response.status_code, 204)
        video.refresh_from_db()
        self.assertEqual(video.availability, Availability.AVAILABLE)
        self.assertEqual(video.hold_reason, "")
        job = Job.objects.get(kind="youtube.download", state__in=JobState.active())
        self.assertTrue(job.payload.get("approved"))

    def test_approving_an_unknown_id_is_404(self):
        response = self.client.post(
            reverse("action_approve_video", args=["nosuchvideo"]))
        self.assertEqual(response.status_code, 404)

    def test_get_is_rejected(self):
        video = _video(availability=Availability.NEEDS_REVIEW)
        response = self.client.get(
            reverse("action_approve_video", args=[video.pk]))
        self.assertEqual(response.status_code, 405)


class DashboardPanelTests(TestCase):
    def test_held_entries_appear_with_both_links(self):
        _video(video_id="shown0000001", title="Some Lyric Video",
               availability=Availability.NEEDS_REVIEW,
               hold_reason="YouTube lists no track")
        html = self.client.get(reverse("dashboard")).content.decode()
        self.assertIn("not downloaded", html)
        self.assertIn("Some Lyric Video", html)
        self.assertIn("YouTube lists no track", html)
        self.assertIn("youtube.com/watch?v=shown0000001", html)
        self.assertIn("music.youtube.com/watch?v=shown0000001", html)

    def test_nothing_is_rendered_when_nothing_is_held(self):
        _video(video_id="quiet0000001")
        html = self.client.get(reverse("dashboard")).content.decode()
        self.assertNotIn("not downloaded", html)
