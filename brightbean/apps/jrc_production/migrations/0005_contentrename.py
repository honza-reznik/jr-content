# JR Content: přidáno 2026-11-07 (blok P1-B38), AGPL-3.0.
import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("composer", "0023_platformpost_youtube_video_checked_at"),
        ("jrc_production", "0004_shotlist_thumbnail_items"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="ContentRename",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("old_title", models.CharField(blank=True, default="", max_length=255)),
                ("new_title", models.CharField(max_length=255)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "post",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE, related_name="jrc_renames", to="composer.post"
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ["created_at", "id"],
                "indexes": [models.Index(fields=["post", "created_at"], name="jrc_rename_post_idx")],
            },
        ),
    ]
