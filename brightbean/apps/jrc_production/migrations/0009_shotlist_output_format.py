# JR Content: přidáno 2026-11-14 (blok P2-B14), AGPL-3.0.
"""Shotlist per format: each item belongs to the shotlist of one format of the content.

The existing shotlist becomes the list of the content's first format (in the order long form, short
form, story, post), so nothing moves for content with one format. Content without outputs and the
automatic thumbnail item keep an empty format: they show in the first list.

Going back drops the item formats, so it is refused before anything is dropped while that would lose
data: the outputs could not go back either (``output_reversal_blockers``), or an item sits on a list
other than the first one, which the forward step above would not restore (P2-B14 review).
"""

from django.db import migrations, models
from django.db.migrations.exceptions import IrreversibleError

ORDER = ["long_form", "short_form", "story", "post"]


def _first_formats(PlatformPost):
    """``{post_id: format}`` of each content's first format (long form, short form, story, post)."""
    first = {}
    for post_id, output_format in PlatformPost._base_manager.values_list("post_id", "output_format"):
        rank = ORDER.index(output_format) if output_format in ORDER else len(ORDER)
        if post_id not in first or rank < first[post_id][0]:
            first[post_id] = (rank, output_format)
    return {post_id: output_format for post_id, (_rank, output_format) in first.items()}


def assign_first_format(apps, schema_editor):
    ShotlistItem = apps.get_model("jrc_production", "ShotlistItem")
    PlatformPost = apps.get_model("composer", "PlatformPost")
    by_format = {}
    for post_id, output_format in _first_formats(PlatformPost).items():
        by_format.setdefault(output_format, []).append(post_id)
    for output_format, post_ids in by_format.items():
        for start in range(0, len(post_ids), 500):
            ShotlistItem.objects.filter(post_id__in=post_ids[start : start + 500], kind="item").update(
                output_format=output_format
            )


def refuse_lossy_reversal(apps, schema_editor):
    """Runs before the field is removed when going back, so a refusal leaves the data as it is."""
    from apps.composer.output_formats import output_reversal_blockers

    ShotlistItem = apps.get_model("jrc_production", "ShotlistItem")
    PlatformPost = apps.get_model("composer", "PlatformPost")
    blockers = output_reversal_blockers(PlatformPost)
    first = _first_formats(PlatformPost)
    rows = ShotlistItem.objects.exclude(output_format="").values_list("post_id", "kind", "output_format")
    split = sum(
        1
        for post_id, kind, output_format in rows.iterator(chunk_size=500)
        if kind != "item" or output_format != first.get(post_id)
    )
    if split:
        blockers.append(f"{split} shotlist item(s) are on a format's list other than the first one")
    if blockers:
        raise IrreversibleError(
            "Cannot go back before shotlists per format (jrc_production 0009) without losing data: "
            + "; ".join(blockers)
            + ". Remove the extra outputs and move the items to the first list first."
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
        migrations.RunPython(assign_first_format, refuse_lossy_reversal),
    ]
