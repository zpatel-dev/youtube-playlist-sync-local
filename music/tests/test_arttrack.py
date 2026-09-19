"""Tests for the art-track classifier.

Every fixture below is real metadata read from YouTube with yt-dlp while
curating this playlist, not invented. The pairs matter most: the same song as
an art track and as a video upload, which is exactly the case the rule has to
separate and the case a human eye gets wrong.
"""
from django.test import SimpleTestCase

from music.ingest import arttrack


#: Confirmed art tracks. The first four are `- Topic` channels; the rest are
#: official artist channels, which is why the channel name alone is not enough.
ART_TRACKS = [
    ("Amar Arshi - Topic", {
        "title": "Kala Chashma", "track": "Kala Chashma",
        "artist": "Amar Arshi, Badshah, Neha Kakkar, Indeep Bakshi",
        "album": "Baar Baar Dekho", "uploader": "Amar Arshi - Topic"}),
    ("Sunil Grover - Topic", {
        "title": "Mere Husband Mujhko Piyar Nahin Karte",
        "track": "Mere Husband Mujhko Piyar Nahin Karte",
        "artist": "Sunil Grover", "album": "Mere Husband Mujhko Piyar Nahin Karte",
        "uploader": "Sunil Grover - Topic"}),
    ("Mohammed Rafi - Topic", {
        "title": "Main Zindagi Ka Saath Nibhata Chala Gaya Revival",
        "track": "Main Zindagi Ka Saath Nibhata Chala Gaya Revival",
        "artist": "Mohd Rafi", "album": "Hum Dono",
        "uploader": "Mohammed Rafi - Topic"}),
    ("Suman Kalyanpur - Topic", {
        "title": "Tujhe Pyar Karte Hain", "track": "Tujhe Pyar Karte Hain",
        "artist": "Suman Kalyanpur, Mohammed Rafi", "album": "April Fool",
        "uploader": "Suman Kalyanpur - Topic"}),
    ("ROSE's own channel", {
        "title": "APT.", "track": "APT.", "artist": "ROSÉ, Bruno Mars",
        "album": "rosie", "uploader": "ROSÉ"}),
    ("Sanju Rathod's own channel", {
        "title": "Gulabi Sadi", "track": "Gulabi Sadi",
        "artist": "Sanju Rathod, G - SPXRK", "album": "Gulabi Sadi",
        "uploader": "Sanju Rathod SR"}),
    ("Aditya Gadhvi's own channel", {
        "title": "Khalasi | Coke Studio Bharat",
        "track": "Khalasi | Coke Studio Bharat",
        "artist": "Aditya Gadhvi, Achint",
        "album": "Khalasi | Coke Studio Bharat", "uploader": "Aditya Gadhvi"}),
]

#: Confirmed video uploads. Note the third and fourth: the same songs as two of
#: the art tracks above, on the same channel in one case.
VIDEO_UPLOADS = [
    ("the official Gulabi Sadi video", {
        "title": "#GulabiSadi ( गुलाबी साडी ) | Official #video | Sanju Rathod "
                 "| G-Spark | Prajakta | #marathi Song",
        "uploader": "Sanju Rathod SR"}),
    ("the Coke Studio channel's Khalasi", {
        "title": "Coke Studio Bharat | Khalasi | Aditya Gadhvi x Achint",
        "uploader": "Coke Studio India"}),
    ("a lyric video", {
        "title": "Lyrical: Ranjha | Queen | Kangana Ranaut, Raj Kumar Rao | "
                 "Rupesh Kumar Ram | T-Series",
        "uploader": "T-Series"}),
    ("a plain user upload", {
        "title": "har fikr ko dhuay mein udata chala gaya",
        "uploader": "Vaibhav Garg"}),
    ("a full-video film clip", {
        "title": "Channa Ve - Full Video | Bhoot - Part One: The Haunted Ship",
        "uploader": "Zee Music Company"}),
]


class ClassifyTests(SimpleTestCase):
    def test_real_art_tracks_are_accepted(self):
        for label, meta in ART_TRACKS:
            with self.subTest(label):
                self.assertEqual(arttrack.hold_reason(meta), "", f"{label} was rejected")

    def test_real_video_uploads_are_held(self):
        for label, meta in VIDEO_UPLOADS:
            with self.subTest(label):
                self.assertTrue(arttrack.hold_reason(meta), f"{label} was accepted")

    def test_the_same_song_is_split_correctly(self):
        """Gulabi Sadi, both ways, from the same uploader.

        This is the case that motivated the whole feature: judging by channel
        or by title puts these two on the same side.
        """
        art = dict(ART_TRACKS[5][1])
        video = dict(VIDEO_UPLOADS[0][1])
        self.assertEqual(art["uploader"], video["uploader"])
        self.assertEqual(arttrack.hold_reason(art), "")
        self.assertTrue(arttrack.hold_reason(video))

    def test_an_official_channel_is_not_rejected_for_lacking_topic(self):
        """Requiring `- Topic` would throw away ROSE's own upload of APT."""
        meta = ART_TRACKS[4][1]
        self.assertNotIn("topic", meta["uploader"].lower())
        self.assertEqual(arttrack.hold_reason(meta), "")

    def test_topic_channel_passes_without_music_fields(self):
        """A `- Topic` upload is a label delivery even if the fields are thin."""
        self.assertEqual(
            arttrack.hold_reason({"title": "Something", "uploader": "X - Topic"}), "")

    def test_reason_names_what_is_missing(self):
        reason = arttrack.hold_reason({"title": "Some Song", "uploader": "Someone"})
        self.assertIn("track", reason)
        self.assertIn("artist", reason)
        self.assertIn("album", reason)

    def test_partial_metadata_is_held(self):
        """An artist with no track name is not enough to download unattended."""
        self.assertTrue(arttrack.hold_reason(
            {"title": "X", "artist": "Someone", "uploader": "Someone"}))

    def test_empty_metadata_does_not_raise(self):
        self.assertTrue(arttrack.hold_reason({}))

    def test_none_values_are_treated_as_absent(self):
        self.assertTrue(arttrack.hold_reason(
            {"title": "X", "track": None, "artist": None, "uploader": None}))

