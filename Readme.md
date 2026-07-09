# YouTube Playlist Sync

A lightweight Django app (built to run on a Raspberry Pi) that mirrors a YouTube
playlist locally: it tracks each video, downloads audio with **yt-dlp**, and tags
it with **Shazam** (Gemini fallback).

## How it works (architecture)

Slow work never runs inside an HTTP request. A view only *enqueues* a job and
returns instantly. A small pool of **worker threads inside the same process**
drains the queue (downloads/tagging), and the browser gets live updates over
**SSE** — no second process, no Redis, no Channels.

```
Browser ──POST /actions/… (htmx)──►  gunicorn ──creates──► Job (QUEUED)   [returns 204 instantly]
                                        │  └─ worker threads ──claim──► download / tag / resync
Browser ◄──── SSE /events/ ────────────┘     (they update DB rows)
        (on "update", htmx re-fetches the rows / pills / job fragments)
```

- **No stuck POSTs** — every action returns immediately (HTTP 204 + a toast).
- **Live updates** — the dashboard opens one `/events/` stream; when the data
  changes the server sends a bare `update` and htmx re-fetches the changed
  fragments. New videos appear on their own.
- **Fast page load** — Bootstrap/icons/htmx are self-hosted (no CDN) and served
  gzipped by WhiteNoise; the yt-dlp version badge lazy-loads after paint.
- **SQLite in WAL mode** — lets the request threads and worker threads share
  `db.sqlite3` safely; job claiming is atomic (already configured in `settings.py`).

Key pieces:

| File | Role |
|------|------|
| `playlist/models.py` → `Job` | the background task queue |
| `playlist/services/job_service.py` | `enqueue()` — called by views |
| `playlist/services/job_runner.py` | executes a job (reuses the download/tag/youtube services) |
| `playlist/services/worker.py` | the inline worker thread pool (started from `wsgi.py`) |
| `playlist/views.py` → `stream_status` | the SSE change-signal endpoint |
| `playlist/templates/playlist/_rows.html`, `_status_pills.html`, `_job_status.html` | fragments re-fetched on update |

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # then edit PLAYLIST_URL + GEMINI_API_KEY (required)
python manage.py migrate
python manage.py collectstatic --noinput
```

## Run

**One** process — the workers run inside it.

**Development**
```bash
python manage.py runserver 0.0.0.0:8000   # threaded: serves SSE and runs the job workers
```

**Production (Raspberry Pi, systemd)**
```bash
sudo cp deploy/music_manager.service /etc/systemd/system/
sudo visudo -f /etc/sudoers.d/music-manager   # paste deploy/sudoers-music-manager (for the Update button)
sudo systemctl daemon-reload
sudo systemctl enable --now music_manager
```
gunicorn runs with the threaded worker (`-k gthread`). **Keep `--workers 1`** and
scale job concurrency with `WORKER_THREADS` in `.env` — the job threads live in
the web process, so extra gunicorn workers would each start their own pool.
Don't use `--preload` (threads don't survive gunicorn's fork).

> Note: `settings.py` ships with `DEBUG = True`. For a faster/safer production
> run, set it to `False` (WhiteNoise then serves the pre-compressed assets).

## CLI / batch commands

```bash
python manage.py sync_playlist     # fetch playlist + download/tag everything new
python manage.py process_tagging   # tag any DOWNLOADED/TAGGING tracks
```

## Fetch and store playlist videos from a shell

```bash
python manage.py shell
```
```python
from django.conf import settings
from playlist.services.youtube_service import YoutubeService
from playlist.models import Video

service = YoutubeService(playlist_url=settings.PLAYLIST_URL)
service.fetch_playlist_videos()
print(f"{Video.objects.count()} videos in the database.")
```
