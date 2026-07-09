"""
WSGI config for music_manager project.

It exposes the WSGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/5.2/howto/deployment/wsgi/
"""

import logging
import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'music_manager.settings')

application = get_wsgi_application()

# Start the inline background worker pool. This module is imported only when the
# server actually runs (gunicorn / runserver) — NOT during migrate/shell/etc. —
# so it's the right place to launch the job threads. Never use gunicorn --preload
# (threads don't survive the fork); keep --workers 1 and scale with WORKER_THREADS.
try:
    from playlist.services.worker import start_workers
    start_workers()
except Exception:  # noqa: BLE001 - a worker startup hiccup must not stop the web server
    logging.getLogger('django').exception('Failed to start inline job workers')
