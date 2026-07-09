from django.urls import path, include

urlpatterns = [
    # The API will be at /api/videos, etc.
    path('api/', include('playlist.urls')),
    # The UI dashboard will be at the root URL '/'
    path('', include('playlist.urls')),
]
