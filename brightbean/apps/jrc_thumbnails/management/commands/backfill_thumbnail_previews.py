from django.core.management.base import BaseCommand

from apps.jrc_thumbnails.models import ChannelThumbnail
from apps.jrc_thumbnails.services import generate_small_image


class Command(BaseCommand):
    help = "Generate missing small previews for existing channel thumbnails."

    def handle(self, *args, **options):
        queryset = ChannelThumbnail.objects.filter(small_image="").order_by("created_at", "pk")
        total = queryset.count()
        created = 0
        failed = 0
        self.stdout.write(f"Generating previews for {total} thumbnail(s)...")
        for thumbnail in queryset.iterator():
            try:
                with thumbnail.image.open("rb") as original:
                    preview = generate_small_image(original)
                thumbnail.small_image.save(f"{thumbnail.id}-small.jpg", preview, save=True)
            except Exception as exc:  # Keep processing other versions; report corrupt or missing source files.
                failed += 1
                self.stderr.write(f"Could not create preview for {thumbnail.pk}: {exc}")
            else:
                created += 1
        self.stdout.write(self.style.SUCCESS(f"Created {created} preview(s); {failed} failed."))
