from rest_framework import serializers
from .models import Video, LocalTrack

class LocalTrackSerializer(serializers.ModelSerializer):
    class Meta:
        model = LocalTrack
        fields = [
            'processing_status', 
            'local_path', 
            'downloaded_at', 
            'md5_hash', 
            'fail_count',
            'updated_at'
        ]

class VideoSerializer(serializers.ModelSerializer):
    # This nests the LocalTrack details directly inside the Video JSON object.
    # 'read_only=True' means we can't create a LocalTrack via this serializer.
    local_track = LocalTrackSerializer(read_only=True)

    class Meta:
        model = Video
        fields = [
            'id', 
            'title', 
            'uploader', 
            'duration', 
            'url', 
            'status', 
            'last_check_at',
            'local_track' # The nested serializer
        ]