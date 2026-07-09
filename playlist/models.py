from django.db import models
from django.utils.translation import gettext_lazy as _

class Video(models.Model):
    """
    Represents the canonical information for a video as it exists on YouTube.
    This model's responsibility is to store data retrieved from the YouTube API.
    """
    class VideoStatus(models.TextChoices):
        AVAILABLE = 'AVAILABLE', _('Available')
        UNAVAILABLE = 'UNAVAILABLE', _('Unavailable')
        PRIVATE = 'PRIVATE', _('Private') # Added for more granular status
        DELETED = 'DELETED', _('Deleted') # Added for more granular status

    id = models.CharField(max_length=11, primary_key=True, help_text="YouTube video ID")
    title = models.CharField(max_length=255)
    uploader = models.CharField(max_length=255, null=True, blank=True)
    duration = models.PositiveIntegerField(null=True, blank=True, help_text="Duration in seconds")
    url = models.URLField(max_length=2048, unique=True)
    status = models.CharField(
        max_length=20,
        choices=VideoStatus.choices,
        default=VideoStatus.AVAILABLE
    )

    # Timestamps related to checking the video on YouTube
    last_check_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.title} ({self.id})"

    class Meta:
        ordering = ['-created_at']
        verbose_name = "YouTube Video"


class LocalTrack(models.Model):
    """
    Represents a downloaded and processed audio file on the local system.
    This model's responsibility is to track the state of the local file,
    from download through tagging.
    """
    class ProcessingStatus(models.TextChoices):
        PENDING = 'PENDING', _('Pending Download')
        DOWNLOADING = 'DOWNLOADING', _('Downloading')
        DOWNLOADED = 'DOWNLOADED', _('Downloaded, Awaiting Tagging')
        TAGGING = 'TAGGING', _('Tagging with Shazam')
        COMPLETED = 'COMPLETED', _('Processing Complete')
        FAILED = 'FAILED', _('Processing Failed')

    video = models.OneToOneField(
        Video,
        on_delete=models.CASCADE,
        primary_key=True,
        related_name='local_track'
    )
    local_path = models.CharField(max_length=1024, blank=True, help_text="The absolute path to the local mp3 file.")
    processing_status = models.CharField(
        max_length=20,
        choices=ProcessingStatus.choices,
        default=ProcessingStatus.PENDING
    )
    downloaded_at = models.DateTimeField(null=True, blank=True)
    md5_hash = models.CharField(max_length=32, null=True, blank=True)

    # Retry logic fields are a concern of the local processing, not the video itself
    retry_at = models.DateTimeField(null=True, blank=True)
    fail_count = models.PositiveIntegerField(default=0)

    # Timestamps related to the local file
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Track for: {self.video.title}"

    class Meta:
        ordering = ['-created_at']
        verbose_name = "Local Audio Track"


class Job(models.Model):
    """
    A unit of background work drained by the inline worker pool (see
    playlist/services/worker.py).

    The web request only ever *enqueues* a Job (status QUEUED) and returns
    immediately, so no HTTP request ever blocks on yt-dlp / Shazam. Worker threads
    running inside the web process claim QUEUED jobs, do the slow work, and write
    progress back here (and onto the Video/LocalTrack rows). The SSE endpoint
    watches these rows to signal the browser to refresh.
    """

    class JobType(models.TextChoices):
        RESYNC = 'RESYNC', _('Resync playlist')
        DOWNLOAD = 'DOWNLOAD', _('Download + tag video')
        TAG_ALL = 'TAG_ALL', _('Process pending tagging')
        DELETE = 'DELETE', _('Delete local track')
        UPDATE_YTDLP = 'UPDATE_YTDLP', _('Update yt-dlp')
        AUTO_SYNC = 'AUTO_SYNC', _('Auto-sync + download')

    class Status(models.TextChoices):
        QUEUED = 'QUEUED', _('Queued')
        RUNNING = 'RUNNING', _('Running')
        SUCCESS = 'SUCCESS', _('Success')
        FAILED = 'FAILED', _('Failed')

    job_type = models.CharField(max_length=20, choices=JobType.choices)
    # Null for playlist-wide jobs (resync, tag-all, update-ytdlp).
    video = models.ForeignKey(
        Video,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='jobs',
    )
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.QUEUED)
    message = models.TextField(blank=True, default='', help_text="Progress note or error detail.")

    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            # The worker claims the oldest queued job; the UI counts active jobs.
            models.Index(fields=['status', 'created_at']),
        ]

    def __str__(self):
        target = self.video_id or 'playlist'
        return f"{self.job_type} [{self.status}] ({target})"

    @property
    def is_active(self) -> bool:
        return self.status in (self.Status.QUEUED, self.Status.RUNNING)