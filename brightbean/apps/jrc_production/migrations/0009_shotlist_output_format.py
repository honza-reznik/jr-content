# JR Content: přidáno 2026-11-14 (blok P2-B14), AGPL-3.0.
"""Shotlist per format: each item belongs to the shotlist of one format of the content.

The existing shotlist becomes the list of the content's first format (in the order long form, short
form, story, post), so nothing moves for content with one format. Content without outputs and the
automatic thumbnail item keep an empty format: they show in the first list.
"""

from django.db import migrations, models

ORDER = ["long_form", "short_form", "story", "post"]


def assign_first_format(apps, schema_editor):
    ShotlistItem = apps.get_model("jrc_production", "ShotlistItem")
    PlatformPost = apps.get_model("composer", "PlatformPost")
    first = {}
    for post_id, output_format in PlatformPost._base_manager.values_list("post_id", "output_format"):
        rank = ORDER.index(output_format) if output_format in ORDER else len(ORDER)
        if post_id not in first or rank < first[post_id][0]:
            first[post_id] = (rank, output_format)
    by_format = {}
    for post_id, (_rank, output_format) in first.items():
        by_format.setdefault(output_format, []).append(post_id)
    for output_format, post_ids in by_format.items():
        for start in range(0, len(post_ids), 500):
            ShotlistItem.objects.filter(post_id__in=post_ids[start : start + 500], kind="item").update(
                output_format=output_format
            )


class Migration(migrations.Migration):
    dependencies = [
        ("jrc_production", "0008_video_deletion_request"),
        ("composer", "0025_platformpost_output_format"),
    ]

    operations = [
        migrations.AddField(
            model_name="shotlistitem",
            name="output_format",
            field=models.CharField(blank=True, default="", max_length=20),
        ),
        migrations.RunPython(assign_first_format, migrations.RunPython.noop),
    ]
