"""Deleting a track on purpose: the file, the row, and everything pointing at it.

The rest of this package never deletes — duplicates are parked, moves are
revertible, a vanished file becomes MISSING rather than a `DELETE`. This module
is the one exception, reached only when a person asks for it by name and
confirms, and it is deliberately the only place that calls `os.remove` on a
library file.

**The file has to go with the row.** Every scan root is walked on a schedule,
so deleting only the database row means the next scan rediscovers the file and
recreates it — the track appears to come back by itself.

**A YouTube-sourced track comes back anyway.** Removing the `YoutubeVideo` row
makes the next playlist sync see that video as never downloaded and fetch it
again. Nothing here can prevent that; only removing it from the playlist can,
which is why `Removal.youtube_url` is reported back and the confirmation panel
links to the video inside the playlist.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

from django.conf import settings
from django.utils import timezone

from music.models import Job, JobState, Track

log = logging.getLogger("music.library.remove")

#: Appended to, never rewritten. For a scanned file the audio is unrecoverable,
#: so this is the only record that the track ever existed — what it was, where
#: it lived, and where it came from.
LEDGER_NAME = "deleted-tracks.jsonl"


@dataclass
class Removal:
    """What a delete did, or would do."""

    track_id: int
    path: str
    label: str
    size_bytes: int = 0
    file_existed: bool = False
    youtube_id: str = ""
    youtube_url: str = ""
    jobs_cancelled: int = 0
    folders_removed: list[str] = field(default_factory=list)

    @property
    def recoverable(self) -> bool:
        """Whether the audio can be fetched again from its source."""
        return bool(self.youtube_url)


def ledger_path() -> Path:
    """Where removals are recorded: beside the database, not in the library.

    Not `LIBRARY_ROOT` itself, because a scan would then pick the ledger up as
    a file to manage, and not its parent either — that is someone else's
    directory, may not be writable, and for a root like `/media/music` is the
    mount point. `BASE_DIR` already holds `db.sqlite3`, so it is the one place
    the app is certain it can write.
    """
    return Path(settings.BASE_DIR) / LEDGER_NAME


def remove_track(track: Track) -> Removal:
    """Delete `track`'s file and row. The caller holds the track lock.

    Ordering is what makes this safe to interrupt: the ledger entry is flushed
    to disk *before* anything is destroyed, so a crash halfway leaves a record
    of what was being removed rather than a silently missing file.
    """
    path = Path(track.path)
    video = getattr(track, "youtube_video", None)
    exists = path.exists()

    removal = Removal(
        track_id=track.pk,
        path=str(path),
        label=_label(track),
        size_bytes=path.stat().st_size if exists else 0,
        file_existed=exists,
        youtube_id=video.video_id if video else "",
        youtube_url=video.url if video else "",
    )

    _record(track, removal)

    # Cancel first: a queued job naming this track would otherwise run against
    # a row that no longer exists and report a confusing failure.
    removal.jobs_cancelled = _cancel_jobs(track.pk)

    if video is not None:
        video.delete()
    if exists:
        os.remove(path)
    track.delete()

    removal.folders_removed = _prune_empty(path.parent)
    log.warning(
        "deleted track %s (%s), %s byte(s), %s job(s) cancelled",
        removal.track_id, removal.label, removal.size_bytes, removal.jobs_cancelled,
    )
    return removal


def _label(track: Track) -> str:
    title = (track.title or "").strip()
    artist = (track.artist or track.album_artist or "").strip()
    if title and artist:
        return f"{artist} - {title}"
    return title or Path(track.path).name


def _cancel_jobs(track_id: int) -> int:
    cancelled = 0
    for job in Job.objects.filter(state__in=JobState.active()):
        if (job.payload or {}).get("track_id") == track_id:
            job.state = JobState.CANCELLED
            job.message = "track deleted from the library"
            job.finished_at = timezone.now()
            job.save(update_fields=["state", "message", "finished_at"])
            cancelled += 1
    return cancelled


def _record(track: Track, removal: Removal) -> None:
    """Append to the ledger and fsync. A failure here does not stop the delete —
    the person asked for it — but it is logged loudly, because losing the record
    is the only part of this that cannot be reconstructed."""
    entry = {
        "deleted_at": timezone.now().isoformat(),
        "track_id": track.pk,
        "title": track.title,
        "artist": track.artist,
        "album": track.album,
        "album_artist": track.album_artist,
        "track_no": track.track_no,
        "disc_no": track.disc_no,
        "year": track.year,
        "duration": track.duration,
        "bitrate": track.bitrate,
        "size_bytes": removal.size_bytes,
        "path": removal.path,
        "previous_path": track.previous_path,
        "state": track.state,
        "identified_by": track.identified_by,
        "confidence": track.confidence,
        "youtube_id": removal.youtube_id,
        "youtube_url": removal.youtube_url,
    }
    try:
        target = ledger_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
    except OSError:
        log.exception("could not write the deletion ledger; deleting anyway")


def _prune_empty(folder: Path) -> list[str]:
    """Remove the folders this delete emptied, album then artist.

    Bounded to two levels and stops at `LIBRARY_ROOT`: a delete tidying up after
    itself must never walk up and remove the library.
    """
    removed: list[str] = []
    root = Path(settings.LIBRARY_ROOT).resolve()
    current = folder
    for _ in range(2):
        try:
            resolved = current.resolve()
            if resolved == root or root not in resolved.parents:
                break
            if not resolved.is_dir() or any(resolved.iterdir()):
                break
            parent = resolved.parent
            resolved.rmdir()
            removed.append(str(resolved))
            current = parent
        except OSError:
            break
    return removed
