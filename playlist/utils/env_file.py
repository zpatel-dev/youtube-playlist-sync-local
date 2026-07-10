"""Read/update the project .env from the app.

Kept deliberately tiny: parse `KEY=value` lines (same format manage.py reads),
and rewrite in place preserving comments, blank lines and key order. Blank values
are the caller's concern — this just writes exactly what it's given.
"""
from pathlib import Path

from django.conf import settings

ENV_PATH = Path(settings.BASE_DIR) / ".env"


def read_env() -> dict:
    """Return the current KEY=value pairs from .env (quotes stripped)."""
    values = {}
    if not ENV_PATH.exists():
        return values
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        key, _, val = s.partition("=")
        val = val.split(" #", 1)[0].split("\t#", 1)[0]  # ignore inline comments
        values[key.strip()] = val.strip().strip("\"'")
    return values


def update_env(updates: dict) -> None:
    """Apply `updates` to .env in place; existing lines are edited, new keys appended.

    Comments, blank lines and ordering are preserved so the file stays readable.
    """
    lines = ENV_PATH.read_text(encoding="utf-8").splitlines() if ENV_PATH.exists() else []
    remaining = dict(updates)
    out = []
    for line in lines:
        s = line.strip()
        if s and not s.startswith("#") and "=" in s:
            key = s.split("=", 1)[0].strip()
            if key in remaining:
                out.append(f"{key}={remaining.pop(key)}")
                continue
        out.append(line)
    for key, val in remaining.items():
        out.append(f"{key}={val}")
    ENV_PATH.write_text("\n".join(out) + "\n", encoding="utf-8")
