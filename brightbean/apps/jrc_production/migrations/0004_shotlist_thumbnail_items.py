# JR Content: přidáno 2026-11-04 (blok P1-B09), AGPL-3.0.
"""Give every existing content its automatic "Thumbnail photo" shotlist item."""

import uuid

from django.db import migrations

THUMBNAIL_TEXT = "Thumbnail photo"


def add_thumbnail_items(apps, schema_editor):
    Post = apps.get_model("composer", "Post")
    ShotlistItem = apps.get_model("jrc_production", "ShotlistItem")
    missing = Post.objects.exclude(jrc_shotlist_items__kind="thumbnail").values_list("pk", flat=True)
    batch = []
    for post_id in missing.iterator(chunk_size=500):
        batch.append(
            ShotlistItem(id=uuid.uuid4(), post_id=post_id, kind="thumbnail", text=THUMBNAIL_TEXT, position=0)
        )
        if len(batch) >= 500:
            ShotlistItem.objects.bulk_create(batch, ignore_conflicts=True)
            batch = []
    if batch:
        ShotlistItem.objects.bulk_create(batch, ignore_conflicts=True)


class Migration(migrations.Migration):
    dependencies = [("jrc_production", "0003_shotlist")]

    # Reversing only drops the table in 0003; nothing to undo here.
    operations = [migrations.RunPython(add_thumbnail_items, migrations.RunPython.noop)]
