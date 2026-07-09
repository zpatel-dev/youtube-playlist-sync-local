from django.core.management.base import BaseCommand
from playlist.models import LocalTrack
from playlist.services.tagger_service import TaggerService
import asyncio

class Command(BaseCommand):
    help = 'Process LocalTracks with status DOWNLOADED or TAGGING through TaggerService.'

    def handle(self, *args, **options):
        tracks = LocalTrack.objects.filter(
            processing_status__in=[
                LocalTrack.ProcessingStatus.DOWNLOADED,
                LocalTrack.ProcessingStatus.TAGGING
            ]
        )
        if not tracks.exists():
            self.stdout.write(self.style.SUCCESS('No tracks to process.'))
            return
        for track in tracks:
            self.stdout.write(f'Processing: {track}')
            tagger = TaggerService(track=track)
            try:
                asyncio.run(tagger.tag_and_rename_track())
                self.stdout.write(self.style.SUCCESS(f'Processed: {track}'))
            except Exception as e:
                self.stderr.write(self.style.ERROR(f'Error processing {track}: {e}'))
