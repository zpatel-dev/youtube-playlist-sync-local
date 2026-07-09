"""
Enqueue helper for background jobs.

The web request layer calls `enqueue(...)` and returns immediately — it never
runs yt-dlp/Shazam inline. The inline worker threads consume the queue.
"""
import logging
from typing import Optional, Tuple

from playlist.models import Job, LocalTrack, Video

logger = logging.getLogger("worker")


def enqueue(job_type: str, video: Optional[Video] = None, dedup: bool = True) -> Tuple[Job, bool]:
    """
    Create a QUEUED Job. Returns (job, created).

    When `dedup` is True (default) and an equivalent job is already QUEUED or
    RUNNING, the existing job is returned instead of stacking duplicates — so
    mashing "Resync" or double-clicking "Download" can't pile up work.
    """
    if dedup:
        qs = Job.objects.filter(
            job_type=job_type,
            status__in=[Job.Status.QUEUED, Job.Status.RUNNING],
        )
        qs = qs.filter(video=video) if video is not None else qs.filter(video__isnull=True)
        existing = qs.order_by("created_at").first()
        if existing is not None:
            logger.info("enqueue: reusing active %s job #%s", job_type, existing.pk)
            return existing, False

    job = Job.objects.create(job_type=job_type, video=video)
    logger.info("enqueue: created %s job #%s (video=%s)", job_type, job.pk, getattr(video, "id", None))
    return job, True


def enqueue_download(video: Video) -> Tuple[Job, bool]:
    """
    Queue a download+tag job and immediately reflect a 'pending' state on the
    track so the dashboard (via SSE) shows feedback before the worker wakes up.
    """
    track, _ = LocalTrack.objects.get_or_create(video=video)
    if track.processing_status in (
        LocalTrack.ProcessingStatus.FAILED,
        LocalTrack.ProcessingStatus.PENDING,
    ):
        track.processing_status = LocalTrack.ProcessingStatus.PENDING
        track.save(update_fields=["processing_status", "updated_at"])
    return enqueue(Job.JobType.DOWNLOAD, video=video)
