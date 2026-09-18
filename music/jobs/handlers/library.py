"""Library scanning job handlers."""

from __future__ import annotations

import logging

from music.core import events
from music.models import Track, TrackState
from music.jobs import engine
from music.jobs.registry import job

log = logging.getLogger("music.jobs.library")


@job("library.scan_all", max_attempts=2,
     description="Scan every enabled scan root for audio files")
def scan_all(job_obj) -> str:
    from music.library import scanner

    def heartbeat(status: str = "") -> None:
        engine.heartbeat(job_obj, status)

    results = scanner.scan_all(heartbeat=heartbeat)
    total_added = sum(result.added for result in results.values())
    total_seen = sum(result.seen for result in results.values())

    if total_added:
        engine.enqueue(
            "identify.pending", {"limit": 1000}, dedup_key="identify.pending"
        )
    return f"scanned {total_seen} file(s) across {len(results)} root(s); {total_added} new"


@job("library.scan_root", max_attempts=2, description="Scan one directory")
def scan_root(job_obj) -> str:
    from pathlib import Path

    from music.library import scanner

    raw_path = job_obj.payload.get("path")
    if not raw_path:
        return "no path in payload"

    def heartbeat(status: str = "") -> None:
        engine.heartbeat(job_obj, status)

    result = scanner.scan_root(Path(raw_path), heartbeat=heartbeat)
    if result.added:
        engine.enqueue(
            "identify.pending", {"limit": 1000}, dedup_key="identify.pending"
        )
    return (
        f"{raw_path}: {result.seen} seen, {result.added} added, "
        f"{result.updated} updated, {result.errors} error(s)"
    )


@job("library.rehash", max_attempts=1,
     description="Fill in missing content hashes for duplicate detection")
def rehash(job_obj) -> str:
    """Hash files that have none yet, a batch at a time.

    This reads every byte of every file, so it is never folded into a scan. Each
    batch re-enqueues the next, so a restart resumes instead of starting over.
    """
    from music.core.fileio import hash_file

    limit = max(1, int(job_obj.payload.get("limit") or 200))
    pending = list(
        Track.objects.filter(content_hash="")
        .exclude(state=TrackState.MISSING)
        .order_by("id")
        .values_list("id", "path")[:limit]
    )

    hashed = 0
    for index, (track_id, path) in enumerate(pending):
        digest = hash_file(path)
        if digest:
            Track.objects.filter(pk=track_id).update(content_hash=digest)
            hashed += 1
        if index % 25 == 0:
            engine.heartbeat(job_obj)

    remaining = (
        Track.objects.filter(content_hash="")
        .exclude(state=TrackState.MISSING)
        .count()
    )
    if remaining:
        engine.enqueue("library.rehash", {"limit": limit}, dedup_key="library.rehash")
    return f"hashed {hashed} file(s); {remaining} still to do"


@job("library.delete_track", max_attempts=1,
     description="Delete one track: its file, its row, and anything pointing at it")
def delete_track(job_obj) -> str:
    """Carry out a deletion a person confirmed in the UI.

    `max_attempts=1` on purpose. Every other handler is safe to retry; this one
    destroys a file, so a transient failure must surface rather than be tried
    again against a half-removed track.
    """
    from music.core.locks import track_locks
    from music.library import remover

    track_id = job_obj.payload.get("track_id")
    if not track_id:
        return "no track_id in payload"

    with track_locks.acquire(f"track:{track_id}") as acquired:
        if not acquired:  # pragma: no cover - only reachable with a timeout
            return "track busy"
        track = Track.objects.filter(pk=track_id).first()
        if track is None:
            return f"track {track_id} no longer exists"

        removal = remover.remove_track(track)

    events.bump("tracks")
    parts = [f"deleted {removal.label}"]
    if removal.file_existed:
        parts.append(f"{removal.size_bytes / 1048576:.1f}MB freed")
    else:
        parts.append("the file was already gone")
    if removal.jobs_cancelled:
        parts.append(f"{removal.jobs_cancelled} job(s) cancelled")
    if removal.folders_removed:
        parts.append(f"{len(removal.folders_removed)} empty folder(s) removed")
    if removal.youtube_url:
        parts.append("still in the playlist — remove it there or it returns")
    return "; ".join(parts)
