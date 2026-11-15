# JR Content: přidáno 2026-11-14 (blok P2-B15), AGPL-3.0.
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("jrc_video", "0008_videoversion_transcript"),
    ]

    operations = [
        migrations.AddField(
            model_name="videoversion",
            name="copied_from",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="copies",
                to="jrc_video.videoversion",
            ),
        ),
        migrations.AddField(
            model_name="videoversion",
            name="copied_from_label",
            field=models.CharField(blank=True, max_length=300),
        ),
        migrations.AddField(
            model_name="videoversion",
            name="comments_copied",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
    ]
