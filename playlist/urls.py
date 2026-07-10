from django.urls import path, include
from rest_framework.routers import DefaultRouter
from . import views

# --- API ---
router = DefaultRouter()
router.register(r'videos', views.VideoViewSet, basename='video')
router.register(r'tracks', views.LocalTrackViewSet, basename='track')

urlpatterns = [
    # API
    path('api/', include(router.urls)),

    # UI
    path('', views.video_dashboard, name='video_dashboard'),

    # Live updates (Server-Sent Events) + fragments re-fetched on change
    path('events/', views.stream_status, name='stream_status'),
    path('fragments/rows/', views.dashboard_rows, name='dashboard_rows'),
    path('fragments/pills/', views.status_pills, name='status_pills'),
    path('fragments/jobs/', views.job_status, name='job_status'),
    path('ytdlp-version/', views.ytdlp_version, name='ytdlp_version'),

    # Settings (.env editor)
    path('settings/', views.settings_form, name='settings_form'),
    path('actions/update-env/', views.update_env_view, name='update_env'),

    # Action URLs (all enqueue a background Job and return immediately)
    path('actions/resync-playlist/', views.trigger_resync, name='trigger_resync'),
    path('actions/video/<str:pk>/download/', views.trigger_download, name='trigger_download'),
    path('actions/video/<str:pk>/retry/', views.trigger_retry, name='trigger_retry'),
    path('actions/video/<str:pk>/delete/', views.delete_track, name='delete_track'),
    path('actions/update-ytdlp/', views.update_ytdlp, name='update_ytdlp'),
    path('actions/process-tagging/', views.process_tagging_tracks, name='process_tagging_tracks'),
]