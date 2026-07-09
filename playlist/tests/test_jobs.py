"""The background job system: enqueue/dedup, atomic claiming, every handler, worker startup."""
import os
import tempfile
from unittest import mock

from django.test import TestCase
from django.utils import timezone

import playlist.services.worker as worker
from playlist.models import Job, LocalTrack, Video
from playlist.services.job_runner import claim_next_job, run_job
from playlist.services.job_service import enqueue, enqueue_download


def make_video(vid="vid00000001", title="Song", duration=200, status=Video.VideoStatus.AVAILABLE):
    return Video.objects.create(
        id=vid, title=title, url=f"https://youtu.be/{vid}", duration=duration, status=status
    )


class EnqueueTests(TestCase):
    def test_creates_job(self):
        job, created = enqueue(Job.JobType.RESYNC)
        self.assertTrue(created)
        self.assertEqual(job.status, Job.Status.QUEUED)

    def test_dedups_active_playlist_job(self):
        first, c1 = enqueue(Job.JobType.RESYNC)
        second, c2 = enqueue(Job.JobType.RESYNC)
        self.assertTrue(c1)
        self.assertFalse(c2)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(Job.objects.filter(job_type=Job.JobType.RESYNC).count(), 1)

    def test_dedup_disabled_stacks_jobs(self):
        enqueue(Job.JobType.RESYNC)
        enqueue(Job.JobType.RESYNC, dedup=False)
        self.assertEqual(Job.objects.filter(job_type=Job.JobType.RESYNC).count(), 2)

    def test_download_dedup_is_per_video(self):
        v1, v2 = make_video("vid00000001"), make_video("vid00000002")
        enqueue(Job.JobType.DOWNLOAD, video=v1)
        _, created = enqueue(Job.JobType.DOWNLOAD, video=v2)
        self.assertTrue(created)  # different video => not a duplicate
        self.assertEqual(Job.objects.filter(job_type=Job.JobType.DOWNLOAD).count(), 2)

    def test_enqueue_download_marks_track_pending(self):
        video = make_video()
        job, created = enqueue_download(video)
        self.assertTrue(created)
        track = LocalTrack.objects.get(video=video)
        self.assertEqual(track.processing_status, LocalTrack.ProcessingStatus.PENDING)


class ClaimNextJobTests(TestCase):
    def test_claims_jobs_atomically_then_returns_none(self):
        Job.objects.create(job_type=Job.JobType.RESYNC)
        Job.objects.create(job_type=Job.JobType.TAG_ALL)

        first = claim_next_job()
        second = claim_next_job()
        third = claim_next_job()

        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        self.assertIsNone(third)
        self.assertEqual(first.status, Job.Status.RUNNING)
        self.assertEqual(second.status, Job.Status.RUNNING)
        self.assertNotEqual(first.pk, second.pk)


class RunJobTests(TestCase):
    def test_resync_handler(self):
        with mock.patch("playlist.services.job_runner.YoutubeService") as MockYT:
            MockYT.return_value.fetch_playlist_videos.return_value = [1, 2, 3]
            job = run_job(Job.objects.create(job_type=Job.JobType.RESYNC))
        self.assertEqual(job.status, Job.Status.SUCCESS)
        self.assertIn("3", job.message)

    def test_download_handler_downloads_then_tags(self):
        video = make_video()
        downloaded = LocalTrack(video=video, processing_status=LocalTrack.ProcessingStatus.DOWNLOADED)
        with mock.patch("playlist.services.job_runner.DownloaderService") as MockDL, \
             mock.patch("playlist.services.job_runner.TaggerService") as MockTag:
            MockDL.return_value.download_audio.return_value = downloaded
            MockTag.return_value.tag_and_rename_track = mock.AsyncMock()
            job = run_job(Job.objects.create(job_type=Job.JobType.DOWNLOAD, video=video))
            MockTag.return_value.tag_and_rename_track.assert_awaited_once()
        self.assertEqual(job.status, Job.Status.SUCCESS)

    def test_download_handler_short_circuits_when_already_complete(self):
        video = make_video()
        complete = LocalTrack(video=video, processing_status=LocalTrack.ProcessingStatus.COMPLETED)
        with mock.patch("playlist.services.job_runner.DownloaderService") as MockDL, \
             mock.patch("playlist.services.job_runner.TaggerService") as MockTag:
            MockDL.return_value.download_audio.return_value = complete
            job = run_job(Job.objects.create(job_type=Job.JobType.DOWNLOAD, video=video))
            MockTag.assert_not_called()
        self.assertEqual(job.status, Job.Status.SUCCESS)

    def test_download_handler_fails_on_bad_status(self):
        video = make_video()
        failed = LocalTrack(video=video, processing_status=LocalTrack.ProcessingStatus.FAILED)
        with mock.patch("playlist.services.job_runner.DownloaderService") as MockDL:
            MockDL.return_value.download_audio.return_value = failed
            job = run_job(Job.objects.create(job_type=Job.JobType.DOWNLOAD, video=video))
        self.assertEqual(job.status, Job.Status.FAILED)

    def test_tag_all_handler(self):
        v1, v2 = make_video("vid00000001"), make_video("vid00000002")
        LocalTrack.objects.create(video=v1, processing_status=LocalTrack.ProcessingStatus.DOWNLOADED)
        LocalTrack.objects.create(video=v2, processing_status=LocalTrack.ProcessingStatus.TAGGING)
        with mock.patch("playlist.services.job_runner.TaggerService") as MockTag:
            MockTag.return_value.tag_and_rename_track = mock.AsyncMock()
            job = run_job(Job.objects.create(job_type=Job.JobType.TAG_ALL))
        self.assertEqual(job.status, Job.Status.SUCCESS)
        self.assertIn("Tagged 2", job.message)

    def test_delete_handler_removes_file_and_marks_video_deleted(self):
        video = make_video()
        with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as f:
            f.write(b"data")
            path = f.name
        LocalTrack.objects.create(video=video, local_path=path,
                                  processing_status=LocalTrack.ProcessingStatus.COMPLETED)
        job = run_job(Job.objects.create(job_type=Job.JobType.DELETE, video=video))
        self.assertEqual(job.status, Job.Status.SUCCESS)
        self.assertFalse(os.path.exists(path))
        self.assertFalse(LocalTrack.objects.filter(video=video).exists())
        video.refresh_from_db()
        self.assertEqual(video.status, Video.VideoStatus.DELETED)

    def test_update_ytdlp_handler(self):
        with mock.patch("playlist.services.job_runner.subprocess.run") as run_mock, \
             mock.patch("playlist.services.job_runner.subprocess.Popen") as popen_mock:
            job = run_job(Job.objects.create(job_type=Job.JobType.UPDATE_YTDLP))
            run_mock.assert_called_once()
            popen_mock.assert_called_once()
        self.assertEqual(job.status, Job.Status.SUCCESS)

    def test_unknown_job_type_fails(self):
        job = run_job(Job.objects.create(job_type="BOGUS"))
        self.assertEqual(job.status, Job.Status.FAILED)
        self.assertIn("Unknown job type", job.message)


class WorkerStartupTests(TestCase):
    def setUp(self):
        worker._started = False
        self.addCleanup(setattr, worker, "_started", False)

    def test_requeues_stale_running_jobs_and_is_idempotent(self):
        stale = Job.objects.create(
            job_type=Job.JobType.RESYNC, status=Job.Status.RUNNING, started_at=timezone.now()
        )
        with mock.patch("playlist.services.worker.threading.Thread") as MockThread:
            worker.start_workers()
            self.assertEqual(MockThread.call_count, worker.settings.WORKER_THREADS)
            MockThread.reset_mock()
            worker.start_workers()  # second call must be a no-op
            self.assertEqual(MockThread.call_count, 0)

        stale.refresh_from_db()
        self.assertEqual(stale.status, Job.Status.QUEUED)
        self.assertIsNone(stale.started_at)
