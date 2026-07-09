# Test package for the playlist app.
#
# Split by concern so each file stays small and focused:
#   test_units.py     - pure helpers (utils, templatetags) + model behaviour
#   test_services.py  - YoutubeService / DownloaderService / TaggerService / MetadataParser
#                       (external boundaries — yt-dlp, Shazam, Gemini, eyed3 — are mocked)
#   test_jobs.py      - enqueue/dedup, the worker's job claiming + every job handler
#   test_web.py       - dashboard, REST API, SSE, htmx fragments, action endpoints
#   test_commands.py  - the sync_playlist / process_tagging management commands
#   test_project.py   - system checks + "no missing migrations"
#   test_live.py      - real playlist resync smoke test (only runs when RUN_LIVE=1)
