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


class PruneAndDismissTests(TestCase):
    """The two ways a held entry stops being noise."""

    def _sync(self, ids):
        """Run a sync whose playlist contains exactly `ids`."""
        entries = {"entries": [
            {"id": v, "title": f"Entry {v}", "uploader": "Someone",
             "duration": 100, "availability": "public"} for v in ids]}
        with mock.patch.object(youtube, "_ydl", side_effect=lambda o: FakeYDL(entries)):
            return youtube.sync_playlist("https://example.invalid/list")

    def test_a_held_entry_is_dropped_once_it_leaves_the_playlist(self):
        _video(video_id="gone00000001", availability=Availability.NEEDS_REVIEW,
               hold_reason="not a song")
        _video(video_id="stay00000001")
        stats = self._sync(["stay00000001"])
        self.assertEqual(stats["dropped"], 1)
        self.assertFalse(YoutubeVideo.objects.filter(pk="gone00000001").exists())
        self.assertTrue(YoutubeVideo.objects.filter(pk="stay00000001").exists())

    def test_a_held_entry_still_listed_is_kept_and_stays_held(self):
        """The listing always says 'public'; that must not clear our verdict.

        Without this the entry flips back to AVAILABLE on every sync, is
        re-queued, re-probed over the network and re-held — forever.
        """
        _video(video_id="held00000003", availability=Availability.NEEDS_REVIEW,
               hold_reason="not a song")
        stats = self._sync(["held00000003"])
        self.assertEqual(stats["dropped"], 0)
        row = YoutubeVideo.objects.get(pk="held00000003")
        self.assertEqual(row.availability, Availability.NEEDS_REVIEW)
        self.assertEqual(row.hold_reason, "not a song")

    def test_a_downloaded_track_is_never_pruned(self):
        """Only held rows are dropped — a real track outlives its playlist entry."""
        from music.models import Track
        track = Track.objects.create(path="/tmp/kept-by-prune-test.mp3")
        _video(video_id="have00000001", track=track)
        stats = self._sync(["something0x"])
        self.assertEqual(stats["dropped"], 0)
        self.assertTrue(YoutubeVideo.objects.filter(pk="have00000001").exists())

    def test_dismiss_marks_rejected_rather_than_deleting(self):
        _video(video_id="drop00000001", availability=Availability.NEEDS_REVIEW,
               hold_reason="not a song")
        response = self.client.post(
            reverse("action_dismiss_video", args=["drop00000001"]))
        self.assertEqual(response.status_code, 204)
        row = YoutubeVideo.objects.get(pk="drop00000001")
        self.assertEqual(row.availability, Availability.REJECTED)
        self.assertEqual(row.hold_reason, "")

    def test_a_rejection_survives_the_next_sync(self):
        """The whole point. Deleting the row instead would let the listing
        re-create it and hold it again, so Remove would never stick."""
        _video(video_id="nope00000001", availability=Availability.NEEDS_REVIEW)
        self.client.post(reverse("action_dismiss_video", args=["nope00000001"]))
        self._sync(["nope00000001"])          # still in the playlist
        row = YoutubeVideo.objects.get(pk="nope00000001")
        self.assertEqual(row.availability, Availability.REJECTED)

    def test_a_rejected_entry_is_never_queued(self):
        _video(video_id="nope00000002", availability=Availability.REJECTED)
        self.assertEqual(handlers._queue_downloads(), 0)

    def test_a_rejected_entry_is_not_shown_in_the_panel(self):
        _video(video_id="nope00000003", availability=Availability.REJECTED)
        html = self.client.get(reverse("dashboard")).content.decode()
        self.assertNotIn("not downloaded", html)

    def test_a_rejected_entry_is_dropped_once_it_leaves_the_playlist(self):
        _video(video_id="nope00000004", availability=Availability.REJECTED)
        stats = self._sync(["other0000001"])
        self.assertEqual(stats["dropped"], 1)
        self.assertFalse(YoutubeVideo.objects.filter(pk="nope00000004").exists())

    def test_pruning_a_large_playlist_does_not_blow_the_parameter_limit(self):
        """Every other __in in this module is chunked; this one must be too."""
        _video(video_id="stale0000001", availability=Availability.NEEDS_REVIEW)
        stats = self._sync([f"bulk{n:07d}" for n in range(1200)])
        self.assertEqual(stats["seen"], 1200)
        self.assertEqual(stats["dropped"], 1)

    def test_dismiss_refuses_when_a_track_exists(self):
        from music.models import Track
        track = Track.objects.create(path="/tmp/dismiss-guard-test.mp3")
        _video(video_id="hastrack0001", track=track)
        self.client.post(reverse("action_dismiss_video", args=["hastrack0001"]))
        self.assertTrue(YoutubeVideo.objects.filter(pk="hastrack0001").exists())
