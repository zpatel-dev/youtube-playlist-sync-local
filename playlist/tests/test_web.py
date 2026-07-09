"""HTTP surface: dashboard, REST API, SSE stream, htmx fragments, and action endpoints."""
from unittest import mock

from django.test import TestCase
from django.urls import reverse

from playlist import views
from playlist.models import Job, LocalTrack, Video


def make_video(vid="vid00000001", title="Song", duration=200):
    return Video.objects.create(id=vid, title=title, url=f"https://youtu.be/{vid}", duration=duration)


class DashboardTests(TestCase):
    def setUp(self):
        self.video = make_video()

    def test_dashboard_renders(self):
        resp = self.client.get(reverse("video_dashboard"))
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, "playlist/dashboard.html")

    def test_search_sort_and_pagination_params_are_accepted(self):
        for params in ({"q": "Song"}, {"sort": "title"}, {"sort": "evil-value"},
                       {"page": "2"}, {"page": "not-a-number"}):
            with self.subTest(params=params):
                self.assertEqual(self.client.get(reverse("video_dashboard"), params).status_code, 200)

    def test_fragment_endpoints(self):
        for name in ("dashboard_rows", "status_pills", "job_status"):
            with self.subTest(name=name):
                self.assertEqual(self.client.get(reverse(name)).status_code, 200)


class ApiTests(TestCase):
    def setUp(self):
        self.video = make_video()
        LocalTrack.objects.create(video=self.video,
                                  processing_status=LocalTrack.ProcessingStatus.COMPLETED)

    def test_videos_endpoint(self):
        resp = self.client.get("/api/videos/")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data[0]["id"], self.video.id)
        self.assertIn("local_track", data[0])

    def test_tracks_endpoint(self):
        resp = self.client.get("/api/tracks/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.json()), 1)


class YtdlpVersionTests(TestCase):
    def test_version_endpoint_returns_subprocess_output(self):
        views._ytdlp_cache.update(value=None, expires=0.0)  # bust the 1h cache
        with mock.patch("playlist.views.subprocess.run") as run_mock:
            run_mock.return_value.stdout = "2025.09.01\n"
            resp = self.client.get(reverse("ytdlp_version"))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.content, b"2025.09.01")


class SseTests(TestCase):
    def test_stream_starts_with_retry_directive(self):
        resp = self.client.get(reverse("stream_status"))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Content-Type"], "text/event-stream")
        gen = resp.streaming_content
        try:
            self.assertEqual(next(gen), b"retry: 3000\n\n")
        finally:
            gen.close()  # stop the generator so its polling loop doesn't linger


class ActionEndpointTests(TestCase):
    def setUp(self):
        self.video = make_video()

    def _assert_notify_204(self, resp):
        self.assertEqual(resp.status_code, 204)
        self.assertIn("HX-Trigger", resp)

    def test_resync_enqueues_job(self):
        self._assert_notify_204(self.client.post(reverse("trigger_resync")))
        self.assertTrue(Job.objects.filter(job_type=Job.JobType.RESYNC).exists())

    def test_download_enqueues_job_and_marks_pending(self):
        resp = self.client.post(reverse("trigger_download", args=[self.video.id]))
        self._assert_notify_204(resp)
        self.assertTrue(Job.objects.filter(job_type=Job.JobType.DOWNLOAD, video=self.video).exists())
        self.assertEqual(LocalTrack.objects.get(video=self.video).processing_status,
                         LocalTrack.ProcessingStatus.PENDING)

    def test_delete_enqueues_job(self):
        self._assert_notify_204(self.client.post(reverse("delete_track", args=[self.video.id])))
        self.assertTrue(Job.objects.filter(job_type=Job.JobType.DELETE, video=self.video).exists())

    def test_update_ytdlp_enqueues_job(self):
        self._assert_notify_204(self.client.post(reverse("update_ytdlp")))
        self.assertTrue(Job.objects.filter(job_type=Job.JobType.UPDATE_YTDLP).exists())

    def test_process_tagging_enqueues_job(self):
        self._assert_notify_204(self.client.post(reverse("process_tagging_tracks")))
        self.assertTrue(Job.objects.filter(job_type=Job.JobType.TAG_ALL).exists())

    def test_actions_reject_get(self):
        self.assertEqual(self.client.get(reverse("trigger_resync")).status_code, 405)

    def test_download_unknown_video_returns_404(self):
        self.assertEqual(
            self.client.post(reverse("trigger_download", args=["doesnotexist"])).status_code, 404
        )
