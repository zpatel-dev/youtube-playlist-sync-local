"""Project health: Django system checks pass and no model change lacks a migration."""
from io import StringIO

from django.core.management import call_command
from django.test import TestCase


class ProjectHealthTests(TestCase):
    def test_system_check_is_clean(self):
        # Raises SystemCheckError (failing the test) if any check reports an error.
        call_command("check", stdout=StringIO(), stderr=StringIO())

    def test_no_missing_migrations(self):
        try:
            call_command("makemigrations", "--check", "--dry-run",
                         stdout=StringIO(), stderr=StringIO())
        except SystemExit:
            self.fail("Models have changes with no matching migration (run makemigrations).")
