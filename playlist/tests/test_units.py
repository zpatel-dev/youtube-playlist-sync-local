"""Pure helpers (utils + templatetags) and model behaviour — no I/O, no network."""
import hashlib
import os
import tempfile

from django.test import SimpleTestCase, TestCase

from playlist.models import Job, LocalTrack, Video
from playlist.templatetags.playlist_extras import basename, duration, get_item
from playlist.utils.dict_utils import find_deepest_metadata_key
from playlist.utils.file_utils import calculate_md5, sanitize_string


class SanitizeStringTests(SimpleTestCase):
    def test_strips_parenthetical_content(self):
        self.assertEqual(sanitize_string("Song Title (Official Video)"), "Song Title")

    def test_replaces_ampersand_with_and(self):
        self.assertEqual(sanitize_string("Simon & Garfunkel"), "Simon And Garfunkel")

    def test_removes_filesystem_invalid_chars(self):
        self.assertEqual(sanitize_string('a/b:c*d?'), "Abcd")

    def test_transliterates_accents_to_ascii(self):
        self.assertEqual(sanitize_string("Café Déjà"), "Cafe Deja")

    def test_empty_result_falls_back_to_unknown(self):
        self.assertEqual(sanitize_string("()"), "Unknown")
        self.assertEqual(sanitize_string("***"), "Unknown")


class CalculateMd5Tests(SimpleTestCase):
    def test_matches_hashlib_for_real_file(self):
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(b"some audio bytes")
            path = f.name
        self.addCleanup(os.remove, path)
        self.assertEqual(calculate_md5(path), hashlib.md5(b"some audio bytes").hexdigest())

    def test_missing_file_returns_empty_string(self):
        self.assertEqual(calculate_md5("/does/not/exist.mp3"), "")


class FindDeepestMetadataKeyTests(SimpleTestCase):
    def test_finds_text_by_title_in_nested_structure(self):
        data = {"sections": [{"metadata": [{"title": "Album", "text": "Greatest Hits"}]}]}
        self.assertEqual(find_deepest_metadata_key(data, "Album"), "Greatest Hits")

    def test_returns_none_when_absent(self):
        self.assertIsNone(find_deepest_metadata_key({"a": [1, 2, 3]}, "Label"))


class TemplateFilterTests(SimpleTestCase):
    def test_duration_minutes_seconds(self):
        self.assertEqual(duration(200), "3:20")

    def test_duration_hours(self):
        self.assertEqual(duration(3661), "1:01:01")

    def test_duration_invalid_or_negative_is_blank(self):
        self.assertEqual(duration(None), "")
        self.assertEqual(duration("abc"), "")
        self.assertEqual(duration(-5), "")

    def test_get_item(self):
        self.assertEqual(get_item({"x": 1}, "x"), 1)
        self.assertIsNone(get_item({"x": 1}, "missing"))

    def test_basename(self):
        self.assertEqual(basename("/a/b/c.mp3"), "c.mp3")
        self.assertEqual(basename(""), "")


class ModelTests(TestCase):
    def test_job_is_active_property(self):
        self.assertTrue(Job(status=Job.Status.QUEUED).is_active)
        self.assertTrue(Job(status=Job.Status.RUNNING).is_active)
        self.assertFalse(Job(status=Job.Status.SUCCESS).is_active)
        self.assertFalse(Job(status=Job.Status.FAILED).is_active)

    def test_localtrack_defaults_to_pending(self):
        video = Video.objects.create(
            id="vid00000001", title="T", url="https://youtu.be/vid00000001", duration=100
        )
        track = LocalTrack.objects.create(video=video)
        self.assertEqual(track.processing_status, LocalTrack.ProcessingStatus.PENDING)
        self.assertEqual(track.fail_count, 0)

    def test_str_representations(self):
        video = Video.objects.create(
            id="vid00000002", title="Hello", url="https://youtu.be/vid00000002", duration=1
        )
        self.assertIn("Hello", str(video))
        self.assertIn("RESYNC", str(Job(job_type=Job.JobType.RESYNC)))
