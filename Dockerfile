# Simulate a 64-bit Raspberry Pi (Raspberry Pi OS is Debian/aarch64) to test the app
# end-to-end on the same CPU architecture it will actually be deployed on.
#
#   docker buildx build --platform linux/arm64 -t ytsync-pi --load .
#   docker run --rm --platform linux/arm64 ytsync-pi                      # offline test suite
#   docker run --rm --platform linux/arm64 -e RUN_LIVE=1 \
#       -e PLAYLIST_URL='<real playlist url>' ytsync-pi \
#       python manage.py test playlist.tests.test_live -v2               # live smoke test
#
# python:3.12-slim is Debian-based (bookworm), matching Raspberry Pi OS's userland.
FROM python:3.12-slim

# ffmpeg: required by yt-dlp's FFmpegExtractAudio postprocessor (audio -> mp3).
# curl + build-essential: only used if a dependency has no aarch64 wheel and must compile.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        curl \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# settings.py reads these at import time; provide test-safe defaults (override at runtime).
ENV DJANGO_SETTINGS_MODULE=music_manager.settings \
    PLAYLIST_URL=https://www.youtube.com/playlist?list=PLACEHOLDER \
    GEMINI_API_KEY=dummy-key-for-tests \
    OUTPUT_DIRECTORY=/tmp/downloads \
    YTDLP_PATH=yt-dlp \
    PIP_PATH=pip \
    PROJECT_BASE_DIR=/app \
    PYTHONUNBUFFERED=1

# Default: the deterministic, offline feature suite (external services mocked).
CMD ["python", "manage.py", "test", "playlist", "-v", "2"]
