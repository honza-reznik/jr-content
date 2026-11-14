# JR Content: přidáno 2026-11-14 (blok P2-B14), AGPL-3.0.
"""Outputs: a channel copy gets a format and one channel may carry one output per format.

Every existing copy becomes an output with its platform's default format, or the format its upstream
post type already described (``platform_extra["post_type"]``: a YouTube short, an Instagram story…).
No row is created, deleted or merged, so videos, captions, thumbnails, approvals and publications stay
attached exactly as before. The old one-copy-per-channel rule is replaced by one output per channel
and format. Reversing is possible only while no channel carries two outputs of one content.
"""

import apps.composer.output_formats
from django.db import migrations, models


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
    ]
