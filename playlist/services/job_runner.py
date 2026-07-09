"""
Job execution: claim a queued Job and run the matching handler.

This is where the slow work actually happens (in the inline worker threads),
reusing the existing service classes. Handlers update the Video/LocalTrack rows
as they progress; the SSE endpoint watches those rows and signals the browser.
"""
import asyncio
import logging
import os
import subprocess

from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from playlist.models import Job, LocalTrack, Video
from playlist.services.downloader_service import DownloaderService
from playlist.services.job_service import enqueue_download
from playlist.services.tagger_service import TaggerService
from playlist.services.youtube_service import YoutubeService

logger = logging.getLogger("worker")

_MAX_MESSAGE = 2000


def claim_next_job():
    """
    Atomically mark the oldest QUEUED job as RUNNING and return it, or None if idle.

    The conditional UPDATE (…WHERE status=QUEUED) is atomic in SQLite, so multiple
    worker threads (or processes) can never claim the same job.
    """
    while True:
        job = Job.objects.filter(status=Job.Status.QUEUED).order_by("created_at").first()
        if job is None:
            return None
        claimed = Job.objects.filter(pk=job.pk, status=Job.Status.QUEUED).update(
            status=Job.Status.RUNNING, started_at=timezone.now(), message=""
        )
        if claimed:
            job.refresh_from_db()
            return job
        # Another worker grabbed it first — try the next queued job.


def run_job(job: Job) -> Job:
    """Execute a claimed job and record its terminal status."""
    handler = _HANDLERS.get(job.job_type)
    if handler is None:
        return _finish(job, Job.Status.FAILED, f"Unknown job type: {job.job_type}")
    try:
        message = handler(job) or ""
        return _finish(job, Job.Status.SUCCESS, message)
    except Exception as exc:  # noqa: BLE001 - worker must never crash on a bad job
        logger.exception("Job #%s (%s) failed: %s", job.pk, job.job_type, exc)
        return _finish(job, Job.Status.FAILED, str(exc))


def _finish(job: Job, status: str, message: str) -> Job:
    job.status = status
    job.message = (message or "")[:_MAX_MESSAGE]
    job.finished_at = timezone.now()
    job.save(update_fields=["status", "message", "finished_at"])
    return job


# --------------------------------------------------------------------------- #
# Handlers
# --------------------------------------------------------------------------- #

def _handle_resync(job: Job) -> str:
    if not settings.PLAYLIST_URL:
        raise ValueError("PLAYLIST_URL is not configured.")
    service = YoutubeService(playlist_url=settings.PLAYLIST_URL)
    videos = service.fetch_playlist_videos()
    return f"Synced playlist — {len(videos)} video(s) found."


def _videos_needing_download():
    """Available videos with no local track, or failed tracks whose retry time passed."""
    now = timezone.now()
    return Video.objects.filter(
        Q(status=Video.VideoStatus.AVAILABLE) & (
            Q(local_track__isnull=True)
            | (Q(local_track__processing_status=LocalTrack.ProcessingStatus.FAILED)
               & Q(local_track__retry_at__lte=now))
        )
    ).distinct()


def _handle_auto_sync(job: Job) -> str:
    """Periodic full sync: refresh the playlist, then queue a download for anything
    still missing (new videos + failed tracks whose retry time has passed)."""
    if not settings.PLAYLIST_URL:
        raise ValueError("PLAYLIST_URL is not configured.")
    videos = YoutubeService(playlist_url=settings.PLAYLIST_URL).fetch_playlist_videos()

    queued = 0
    for video in _videos_needing_download():
        _, created = enqueue_download(video)   # dedups against already-queued downloads
        if created:
            queued += 1
    return f"Auto-sync: {len(videos)} in playlist, queued {queued} download(s)."


def _handle_download(job: Job) -> str:
    video = job.video
    if video is None:
        raise ValueError("DOWNLOAD job requires a video.")

    downloader = DownloaderService(video=video)
    track = downloader.download_audio()

    if track.processing_status == LocalTrack.ProcessingStatus.DOWNLOADED:
        tagger = TaggerService(track=track)
        asyncio.run(tagger.tag_and_rename_track())
        return f"Downloaded and tagged '{video.title}'."
    if track.processing_status == LocalTrack.ProcessingStatus.COMPLETED:
        return f"'{video.title}' already complete."
    raise RuntimeError(f"Download failed for '{video.title}' (status={track.processing_status}).")


def _handle_tag_all(job: Job) -> str:
    tracks = LocalTrack.objects.filter(
        processing_status__in=[
            LocalTrack.ProcessingStatus.TAGGING,
            LocalTrack.ProcessingStatus.DOWNLOADED,
        ]
    )
    tagged = errors = 0
    for track in tracks:
        try:
            tagger = TaggerService(track=track)
            asyncio.run(tagger.tag_and_rename_track())
            tagged += 1
        except Exception as exc:  # noqa: BLE001
            errors += 1
            logger.exception("Tagging failed for %s: %s", track, exc)
    return f"Tagged {tagged} track(s), {errors} error(s)."


def _handle_delete(job: Job) -> str:
    video = job.video
    if video is None:
        raise ValueError("DELETE job requires a video.")
    track = getattr(video, "local_track", None)
    if track is None:
        return f"No local track to delete for '{video.title}'."

    if track.local_path and os.path.exists(track.local_path):
        os.remove(track.local_path)
    track.delete()
    video.status = Video.VideoStatus.DELETED
    video.save(update_fields=["status", "updated_at"])
    return f"Deleted local track for '{video.title}'."


def _handle_update_ytdlp(job: Job) -> str:
    # Upgrade yt-dlp in the project venv (synchronous so we capture failures).
    subprocess.run(
        [settings.PIP_PATH, "install", "--upgrade", "yt-dlp"],
        cwd=settings.PROJECT_BASE_DIR,
        check=True,
    )
    # Restart the (single) service *after* this job's success is saved, so the
    # record survives the restart. Detached + short delay avoids racing _finish().
    # The worker runs inside this service, so one restart reloads everything.
    subprocess.Popen(  # noqa: S603,S607
        ["sh", "-c", f"sleep 3; sudo systemctl restart {settings.SYSTEMD_SERVICE}"]
    )
    return "yt-dlp updated; restarting…"


_HANDLERS = {
    Job.JobType.RESYNC: _handle_resync,
    Job.JobType.DOWNLOAD: _handle_download,
    Job.JobType.TAG_ALL: _handle_tag_all,
    Job.JobType.DELETE: _handle_delete,
    Job.JobType.UPDATE_YTDLP: _handle_update_ytdlp,
    Job.JobType.AUTO_SYNC: _handle_auto_sync,
}
