#!/usr/bin/env python
"""Django's command-line utility for administrative tasks."""
import os
import sys
from pathlib import Path


def load_dotenv():
    """Load a local .env into os.environ (no dependency). Existing vars win,
    so systemd's EnvironmentFile on the Pi is never overridden."""
    env_path = Path(__file__).resolve().parent / '.env'
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, _, value = line.partition('=')
        os.environ.setdefault(key.strip(), value.strip().strip('"\''))


def main():
    """Run administrative tasks."""
    load_dotenv()
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'music_manager.settings')
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Couldn't import Django. Are you sure it's installed and "
            "available on your PYTHONPATH environment variable? Did you "
            "forget to activate a virtual environment?"
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == '__main__':
    main()
