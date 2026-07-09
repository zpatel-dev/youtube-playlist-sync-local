"""
Inline background worker pool.

Instead of a separate process, a small pool of daemon threads runs *inside* the
web process and drains the Job queue. Started once from `music_manager/wsgi.py`
when the server boots (never during migrate/shell/etc.). Jobs are mostly IO-bound
(download, Shazam; ffmpeg is its own subprocess), so a couple of threads is plenty.

IMPORTANT: run the web server with a SINGLE process (gunicorn --workers 1) and use
WORKER_THREADS for job concurrency. Job claiming is atomic, so extra processes
won't double-process a job — but keeping one process keeps things simple.
"""
import logging
import threading
import time

from django.conf import settings
from django.db import close_old_connections

from playlist.models import Job
from playlist.services.job_service import enqueue

logger = logging.getLogger("worker")

_started = False
_lock = threading.Lock()


def start_workers():
    """Start the worker thread pool once. Idempotent — safe to call on every import."""
    global _started
    with _lock:
        if _started:
            return
        _started = True

    # Recover jobs left RUNNING by a previously crashed process (fresh process =
    # nothing is actually running yet, so any RUNNING row is stale).
    requeued = Job.objects.filter(status=Job.Status.RUNNING).update(
        status=Job.Status.QUEUED, started_at=None
    )
    if requeued:
        logger.info("Requeued %s interrupted job(s) on startup.", requeued)

    count = max(1, int(settings.WORKER_THREADS))
    for i in range(count):
        threading.Thread(target=_worker_loop, name=f"worker-{i + 1}", daemon=True).start()
    logger.info("Started %s inline job worker thread(s).", count)

    # Optional periodic auto-sync (0 = off): enqueue an AUTO_SYNC job on an interval.
    interval = int(getattr(settings, "SYNC_INTERVAL_MINUTES", 0))
    if interval > 0:
        threading.Thread(target=_scheduler_loop, args=(interval,), name="scheduler", daemon=True).start()
        logger.info("Started auto-sync scheduler (every %s min).", interval)


def _scheduler_loop(interval_minutes):
    """Every `interval_minutes`, enqueue one AUTO_SYNC job (resync + queue downloads).
    Dedup means a slow sync won't stack; the first run happens after one interval."""
    while True:
        time.sleep(interval_minutes * 60)
        try:
            close_old_connections()
            _, created = enqueue(Job.JobType.AUTO_SYNC)
            logger.info("Scheduler: %s AUTO_SYNC.", "queued" if created else "skipped (already active)")
        except Exception:  # noqa: BLE001 - the scheduler must never die
            logger.exception("Scheduler loop error; continuing.")


def _worker_loop():
    # Import here so a missing media dep (yt-dlp/shazam on a dev box) disables the
    # worker gracefully instead of crash-looping — the web UI still runs fine.
    try:
        from playlist.services.job_runner import claim_next_job, run_job
    except Exception as exc:  # noqa: BLE001
        logger.warning("Job worker disabled (deps missing: %s). Install requirements to process jobs.", exc)
        return

    poll = settings.WORKER_POLL_SECONDS
    while True:
        try:
            close_old_connections()
            job = claim_next_job()
            if job is None:
                time.sleep(poll)
                continue

            run_job(job)

            # Be a good YouTube citizen after network-heavy jobs.
            if job.job_type in (Job.JobType.DOWNLOAD, Job.JobType.RESYNC):
                time.sleep(settings.WORKER_COOLDOWN_SECONDS)
        except Exception:  # noqa: BLE001 - the loop must never die
            logger.exception("Worker loop error; continuing after a short pause.")
            time.sleep(poll)
