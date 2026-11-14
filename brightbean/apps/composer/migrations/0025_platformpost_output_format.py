# JR Content: přidáno 2026-11-14 (blok P2-B14), AGPL-3.0.
"""Outputs: a channel copy gets a format and one channel may carry one output per format.

Every existing copy becomes an output with its platform's default format, or the format its upstream
post type already described (``platform_extra["post_type"]``: a YouTube short, an Instagram story…).
No row is created, deleted or merged, so videos, captions, thumbnails, approvals and publications stay
attached exactly as before. The old one-copy-per-channel rule is replaced by one output per channel
and format. Reversing is refused, before anything is dropped, while it would lose data: a channel with
two outputs of one content, or an output whose format the old schema cannot keep
(``output_reversal_blockers``).
"""

import apps.composer.output_formats
from django.db import migrations, models
from django.db.migrations.exceptions import IrreversibleError


def fill_formats(apps, schema_editor):
    from apps.composer.output_formats import format_from_post_type

    PlatformPost = apps.get_model("composer", "PlatformPost")
    batch = []
    rows = PlatformPost._base_manager.select_related("social_account").only(
        "id", "platform_extra", "output_format", "social_account__platform"
    )
    for row in rows.iterator(chunk_size=500):
        extra = row.platform_extra if isinstance(row.platform_extra, dict) else {}
        row.output_format = format_from_post_type(row.social_account.platform, extra.get("post_type"))
        batch.append(row)
        if len(batch) >= 500:
            PlatformPost._base_manager.bulk_update(batch, ["output_format"])
            batch = []
    if batch:
        PlatformPost._base_manager.bulk_update(batch, ["output_format"])


def refuse_lossy_reversal(apps, schema_editor):
    """Runs first when going back, so a refusal leaves the schema and the data as they are."""
    from apps.composer.output_formats import output_reversal_blockers

    blockers = output_reversal_blockers(apps.get_model("composer", "PlatformPost"))
    if blockers:
        raise IrreversibleError(
            "Cannot go back before outputs (composer 0025) without losing data: "
            + "; ".join(blockers)
            + ". Remove the extra outputs or change them to their channel's default format first."
        )


class Migration(migrations.Migration):
    dependencies = [
        ("composer", "0024_content_trash"),
    ]

    operations = [
        migrations.AddField(
            model_name="platformpost",
            name="output_format",
            field=apps.composer.output_formats.OutputFormatField(
                blank=True,
                choices=[
                    ("long_form", "Long form"),
                    ("short_form", "Short form"),
                    ("story", "Story"),
                    ("post", "Post"),
                ],
                default="",
                max_length=20,
            ),
        ),
        migrations.RunPython(fill_formats, migrations.RunPython.noop),
        migrations.AlterUniqueTogether(name="platformpost", unique_together=set()),
        migrations.AddConstraint(
            model_name="platformpost",
            constraint=models.UniqueConstraint(
                fields=("post", "social_account", "output_format"), name="uniq_pp_post_account_format"
            ),
        ),
        # Last, so it is the first step when going back (P2-B14 review).
        migrations.RunPython(migrations.RunPython.noop, refuse_lossy_reversal),
    ]
