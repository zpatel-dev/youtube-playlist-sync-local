import json
import logging
import subprocess
import time

from django.conf import settings
from django.core.paginator import EmptyPage, PageNotAnInteger, Paginator
from django.db.models import Count
from django.http import HttpResponse, StreamingHttpResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_POST
from rest_framework import permissions, viewsets

from .models import Job, LocalTrack, Video
from .serializers import LocalTrackSerializer, VideoSerializer
from .services.job_service import enqueue, enqueue_download

logger = logging.getLogger("django")

ACTIVE_JOB_STATUSES = [Job.Status.QUEUED, Job.Status.RUNNING]


# --------------------------------------------------------------------------- #
# REST API (unchanged)
# --------------------------------------------------------------------------- #
class VideoViewSet(viewsets.ReadOnlyModelViewSet):
    """API endpoint that allows videos to be viewed (`list` / `retrieve`)."""

    queryset = Video.objects.all().order_by("-created_at")
    serializer_class = VideoSerializer
    permission_classes = [permissions.AllowAny]


class LocalTrackViewSet(viewsets.ReadOnlyModelViewSet):
    """API endpoint that allows local tracks to be viewed."""

    queryset = LocalTrack.objects.all().order_by("-created_at")
    serializer_class = LocalTrackSerializer
    permission_classes = [permissions.AllowAny]


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
_ytdlp_cache = {"value": None, "expires": 0.0}


def get_ytdlp_version() -> str:
    """Return the yt-dlp version, cached for an hour to keep it off the hot path."""
    now = time.monotonic()
    if _ytdlp_cache["value"] and now < _ytdlp_cache["expires"]:
        return _ytdlp_cache["value"]
    try:
        result = subprocess.run(
            [settings.YTDLP_PATH, "--version"],
            capture_output=True, text=True, check=True, timeout=10,
        )
        version = result.stdout.strip() or "Unknown"
    except Exception:
        version = "Unknown"
    _ytdlp_cache.update(value=version, expires=now + 3600)
    return version


def _hx_notify(message: str, level: str = "success", status: int = 204) -> HttpResponse:
    """Empty HTMX response that fires a client-side toast via the HX-Trigger header."""
    resp = HttpResponse(status=status)
    resp["HX-Trigger"] = json.dumps({"notify": {"level": level, "message": message}})
    return resp


def _status_counts() -> dict:
    """Aggregate status counts for the summary pills (2 cheap grouped queries)."""
    video_status_counts = {
        item["status"]: item["count"]
        for item in Video.objects.values("status").annotate(count=Count("status"))
    }
    track_status_counts = {
        item["processing_status"]: item["count"]
        for item in LocalTrack.objects.values("processing_status").annotate(count=Count("processing_status"))
    }
    total_videos = sum(video_status_counts.values())
    total_tracks = sum(track_status_counts.values())
    track_status_counts["NOT_PROCESSED"] = total_videos - total_tracks
    return {
        "video_status_counts": video_status_counts,
        "track_status_counts": track_status_counts,
        "VideoStatus": Video.VideoStatus,
        "ProcessingStatus": LocalTrack.ProcessingStatus,
    }


# --------------------------------------------------------------------------- #
# Dashboard + live fragments
# --------------------------------------------------------------------------- #
def _paginate_videos(request) -> dict:
    """Search / sort / paginate — shared by the full page and the rows fragment."""
    qs = Video.objects.select_related("local_track").all()

    search_query = request.GET.get("q", "")
    if search_query:
        qs = qs.filter(title__icontains=search_query)

    sort_by = request.GET.get("sort", "-created_at")
    if sort_by not in ["title", "status", "local_track__processing_status", "-created_at"]:
        sort_by = "-created_at"
    qs = qs.order_by(sort_by)

    paginator = Paginator(qs, 25)
    page_number = request.GET.get("page")
    try:
        page = paginator.page(page_number)
    except PageNotAnInteger:
        page = paginator.page(1)
    except EmptyPage:
        page = paginator.page(paginator.num_pages)

    return {
        "video_list": page,
        "page_obj": page,
        "is_paginated": page.has_other_pages(),
        "search_query": search_query,
        "current_sort": sort_by,
        "sort": sort_by,
    }


def _active_jobs():
    return Job.objects.filter(status__in=ACTIVE_JOB_STATUSES).select_related("video")


def video_dashboard(request):
    """Main dashboard: search, sort, pagination, status counts; live via SSE."""
    context = {**_paginate_videos(request), "active_jobs": _active_jobs(), **_status_counts()}
    return render(request, "playlist/dashboard.html", context)


# These fragments are re-fetched by htmx whenever the SSE stream signals a change.
def dashboard_rows(request):
    return render(request, "playlist/_rows.html", _paginate_videos(request))


def status_pills(request):
    return render(request, "playlist/_status_pills.html", _status_counts())


def job_status(request):
    return render(request, "playlist/_job_status.html", {"active_jobs": _active_jobs()})


def ytdlp_version(request):
    """Lazy-loaded (HTMX) so the version subprocess never blocks page render."""
    return HttpResponse(get_ytdlp_version())


# --------------------------------------------------------------------------- #
# Actions — each just enqueues a Job and returns instantly (never blocks)
# --------------------------------------------------------------------------- #
@require_POST
def trigger_resync(request):
    enqueue(Job.JobType.RESYNC)
    return _hx_notify("Playlist resync queued.", "info")


@require_POST
def trigger_download(request, pk):
    video = get_object_or_404(Video, pk=pk)
    enqueue_download(video)
    return _hx_notify(f"Queued download: {video.title}", "info")


@require_POST
def trigger_retry(request, pk):
    """Retry is functionally identical to a fresh download request."""
    return trigger_download(request, pk)


@require_POST
def delete_track(request, pk):
    video = get_object_or_404(Video, pk=pk)
    enqueue(Job.JobType.DELETE, video=video)
    return _hx_notify(f"Queued delete: {video.title}", "warning")


@require_POST
def update_ytdlp(request):
    enqueue(Job.JobType.UPDATE_YTDLP)
    return _hx_notify("yt-dlp update queued — services will restart shortly.", "info")


@require_POST
def process_tagging_tracks(request):
    enqueue(Job.JobType.TAG_ALL)
    return _hx_notify("Tagging queued for all pending tracks.", "info")


# --------------------------------------------------------------------------- #
# Server-Sent Events — a simple "something changed" signal
# --------------------------------------------------------------------------- #
# The stream doesn't render HTML. It just fingerprints the dashboard's data each
# tick and pushes a bare `update` event when it changes; the browser then
# re-fetches the pills / rows / job fragments via htmx (hx-trigger="sse:update").
# This keeps the stream trivial and makes newly-synced videos appear on their own.
def _state_signature():
    """Cheap fingerprint of everything the dashboard shows (3 indexed queries)."""
    return (
        tuple(sorted(Video.objects.values_list("id", "status"))),
        tuple(sorted(LocalTrack.objects.values_list("video_id", "processing_status"))),
        tuple(_active_jobs().values_list("id", "status")),
    )


def stream_status(request):
    """
    SSE endpoint. Plain sync generator — under gunicorn's threaded worker each
    open dashboard just holds one thread that sleeps between polls (no ASGI).
    """
    def event_stream():
        yield "retry: 3000\n\n"  # reconnect after 3s if dropped
        prev = _state_signature()  # page already reflects this; only push future changes
        while True:
            time.sleep(settings.SSE_POLL_SECONDS)
            try:
                current = _state_signature()
            except Exception:  # noqa: BLE001 - never kill the stream on a transient DB error
                logger.exception("SSE state check failed")
                continue
            if current != prev:
                prev = current
                yield "event: update\ndata: 1\n\n"
            else:
                yield ": keepalive\n\n"

    response = StreamingHttpResponse(event_stream(), content_type="text/event-stream")
    response["Cache-Control"] = "no-cache"
    response["X-Accel-Buffering"] = "no"  # disable proxy buffering (nginx, if present)
    return response
