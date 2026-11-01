"""
The jobs page: the log you read when something did not happen.

Two things are worth pinning here. The list must not carry payloads or
tracebacks — those are per-job and unbounded, and the whole reason the detail
lives behind a modal. And its query count must not grow with the number of
jobs, because the Job table is the one that grows fastest on a busy library.
"""

from __future__ import annotations

from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.db import connection
from django.urls import reverse
from django.utils import timezone

from music import views
from music.models import Job, JobState


class JobsPageTests(TestCase):
    def setUp(self):
        now = timezone.now()
        self.succeeded = Job.objects.create(
            kind="library.scan_all", state=JobState.SUCCEEDED,
            message="scanned 9 file(s)", attempts=1,
            started_at=now, finished_at=now,
        )
        self.failed = Job.objects.create(
            kind="identify.track", payload={"track_id": 7},
            state=JobState.FAILED, attempts=3, max_attempts=3,
            error="ProviderError: fpcalc not found on PATH",
            started_at=now, finished_at=now,
        )
        self.queued = Job.objects.create(kind="organize.plan_all")

    def test_page_lists_every_job(self):
        response = self.client.get(reverse("jobs"))
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        for job in (self.succeeded, self.failed, self.queued):
            self.assertIn(job.kind, body)

    def test_list_does_not_carry_payloads(self):
        # A payload is arbitrary JSON and unbounded per row; that is what the
        # detail modal exists for.
        body = self.client.get(reverse("jobs")).content.decode()
        self.assertNotIn("track_id", body)

    def test_a_failure_shows_its_reason(self):
        # A row that says only "Failed" forces the modal open to learn
        # anything, which is worse than a bounded preview.
        body = self.client.get(reverse("jobs")).content.decode()
        self.assertIn("fpcalc not found on PATH", body)

    def test_a_long_error_is_truncated_not_dumped(self):
        # The reason the full error is not carried: bounded in SQL, so a page
        # of rows cannot become a page of tracebacks.
        self.failed.error = "Traceback:" + ("x" * 4000)
        self.failed.save(update_fields=["error"])
        body = self.client.get(reverse("jobs")).content.decode()
        self.assertNotIn("x" * (views.JOB_ERROR_PREVIEW + 1), body)

    def test_state_filter_narrows_the_list(self):
        body = self.client.get(reverse("jobs"), {"state": JobState.FAILED}).content.decode()
        self.assertIn("identify.track", body)
        self.assertNotIn("library.scan_all", body)

    def test_active_filter_covers_queued_and_running(self):
        body = self.client.get(reverse("jobs"), {"state": "active"}).content.decode()
        self.assertIn("organize.plan_all", body)
        self.assertNotIn("library.scan_all", body)

    def test_unknown_state_filter_is_ignored_rather_than_erroring(self):
        # The parameter is as likely to come from a stale URL as from a person.
        response = self.client.get(reverse("jobs"), {"state": "nonsense"})
        self.assertEqual(response.status_code, 200)
        self.assertIn("library.scan_all", response.content.decode())

    def test_htmx_request_returns_only_the_fragment(self):
        response = self.client.get(reverse("jobs"), HTTP_HX_REQUEST="true")
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertIn("job-list", body)
        self.assertNotIn("<html", body.lower())

    def test_query_count_is_flat_as_jobs_accumulate(self):
        with CaptureQueriesContext(connection) as small:
            self.client.get(reverse("jobs"))

        Job.objects.bulk_create([
            Job(kind="identify.track", payload={"track_id": i},
                state=JobState.SUCCEEDED, message="ok" * 50)
            for i in range(120)
        ])

        with CaptureQueriesContext(connection) as large:
            self.client.get(reverse("jobs"))

        self.assertEqual(len(large), len(small),
                         "the jobs list gained a query per row")


class ClearFailedJobsTests(TestCase):
    def setUp(self):
        now = timezone.now()
        self.succeeded = Job.objects.create(
            kind="library.scan_all", state=JobState.SUCCEEDED,
            started_at=now, finished_at=now,
        )
        self.failed = Job.objects.create(
            kind="identify.track", state=JobState.FAILED,
            error="ProviderError", started_at=now, finished_at=now,
        )
        self.queued = Job.objects.create(kind="organize.plan_all")

    def test_clear_removes_only_failed_jobs(self):
        response = self.client.post(reverse("action_clear_failed_jobs"))
        self.assertEqual(response.status_code, 204)
        self.assertIn("Cleared 1 failed job.", response["HX-Trigger"])
        self.assertFalse(Job.objects.filter(pk=self.failed.pk).exists())
        # An active job is never collateral: it may be running right now.
        self.assertTrue(Job.objects.filter(pk=self.queued.pk).exists())
        self.assertTrue(Job.objects.filter(pk=self.succeeded.pk).exists())

    def test_clear_with_nothing_failed_says_so(self):
        self.failed.delete()
        response = self.client.post(reverse("action_clear_failed_jobs"))
        self.assertEqual(response.status_code, 204)
        self.assertIn("No failed jobs to clear.", response["HX-Trigger"])

    def test_clear_is_post_only(self):
        response = self.client.get(reverse("action_clear_failed_jobs"))
        self.assertEqual(response.status_code, 405)
        self.assertTrue(Job.objects.filter(pk=self.failed.pk).exists())

    def test_button_appears_only_while_something_failed(self):
        url = reverse("action_clear_failed_jobs")
        self.assertIn(url, self.client.get(reverse("jobs")).content.decode())
        self.failed.delete()
        self.assertNotIn(url, self.client.get(reverse("jobs")).content.decode())


class JobDetailTests(TestCase):
    def setUp(self):
        now = timezone.now()
        self.job = Job.objects.create(
            kind="identify.track", payload={"track_id": 7},
            dedup_key="identify.track:7",
            state=JobState.FAILED, attempts=3, max_attempts=3,
            error="ProviderError: fpcalc not found on PATH",
            started_at=now, finished_at=now,
        )

    def test_detail_shows_error_payload_and_attempts(self):
        response = self.client.get(reverse("job_detail", args=[self.job.pk]))
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertIn("fpcalc not found on PATH", body)
        self.assertIn("track_id", body)
        self.assertIn("attempt 3 of 3", body)
        self.assertIn("identify.track:7", body)

    def test_detail_is_a_fragment_not_a_full_page(self):
        body = self.client.get(reverse("job_detail", args=[self.job.pk])).content.decode()
        self.assertNotIn("<html", body.lower())

    def test_missing_job_is_404_not_500(self):
        response = self.client.get(reverse("job_detail", args=[999999]))
        self.assertEqual(response.status_code, 404)

    def test_error_text_is_escaped(self):
        # A traceback can quote a filename or a provider response that came
        # from outside this machine; it is rendered as text, never as markup.
        self.job.error = '<img src=x onerror="alert(1)">'
        self.job.save(update_fields=["error"])
        body = self.client.get(reverse("job_detail", args=[self.job.pk])).content.decode()
        self.assertNotIn("<img src=x", body)
        self.assertIn("&lt;img", body)

    def test_detail_costs_one_query(self):
        with CaptureQueriesContext(connection) as queries:
            self.client.get(reverse("job_detail", args=[self.job.pk]))
        self.assertLessEqual(len(queries), 1)
