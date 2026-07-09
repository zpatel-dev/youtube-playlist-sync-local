"""The two management commands, with services mocked so no network/media is touched."""
from unittest import mock

from django.core.management import call_command
from django.test import TestCase

from playlist.models import LocalTrack, Video


def make_video(vid="vid00000001", title="Song", duration=200, status=Video.VideoStatus.AVAILABLE):
    return Video.objects.create(
        id=vid, title=title, url=f"https://youtu.be/{vid}", duration=duration, status=status
    )


class SyncPlaylistCommandTests(TestCase):
    def test_full_sync_downloads_and_tags_new_videos(self):
        video = make_video()  # AVAILABLE, no local track => needs processing
        downloaded = LocalTrack(video=video, processing_status=LocalTrack.ProcessingStatus.DOWNLOADED)

        base = "playlist.management.commands.sync_playlist"
        with mock.patch(f"{base}.YoutubeService") as MockYT, \
             mock.patch(f"{base}.DownloaderService") as MockDL, \
             mock.patch(f"{base}.TaggerService") as MockTag, \
             mock.patch(f"{base}.time.sleep"):  # skip the 5s cooldown
            MockYT.return_value.fetch_playlist_videos.return_value = [video]
            MockDL.return_value.download_audio.return_value = downloaded
            MockTag.return_value.tag_and_rename_track = mock.AsyncMock()

            call_command("sync_playlist")

            MockDL.return_value.download_audio.assert_called_once()
            MockTag.return_value.tag_and_rename_track.assert_awaited_once()

    def test_sync_reports_when_nothing_to_process(self):
        base = "playlist.management.commands.sync_playlist"
        with mock.patch(f"{base}.YoutubeService") as MockYT, \
             mock.patch(f"{base}.DownloaderService") as MockDL:
            MockYT.return_value.fetch_playlist_videos.return_value = []
            call_command("sync_playlist")  # no AVAILABLE videos => early return
            MockDL.assert_not_called()


class ProcessTaggingCommandTests(TestCase):
    def test_processes_downloaded_tracks(self):
        video = make_video()
        LocalTrack.objects.create(video=video, processing_status=LocalTrack.ProcessingStatus.DOWNLOADED)
        with mock.patch("playlist.management.commands.process_tagging.TaggerService") as MockTag:
            MockTag.return_value.tag_and_rename_track = mock.AsyncMock()
            call_command("process_tagging")
            MockTag.return_value.tag_and_rename_track.assert_awaited_once()

    def test_no_tracks_to_process(self):
        with mock.patch("playlist.management.commands.process_tagging.TaggerService") as MockTag:
            call_command("process_tagging")
            MockTag.assert_not_called()
