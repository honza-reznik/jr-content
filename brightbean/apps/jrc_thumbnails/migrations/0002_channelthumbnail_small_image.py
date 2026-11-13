import apps.jrc_thumbnails.models
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("jrc_thumbnails", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="channelthumbnail",
            name="small_image",
            field=models.FileField(
                blank=True,
                default="",
                max_length=255,
                upload_to=apps.jrc_thumbnails.models.thumbnail_path,
            ),
        ),
    ]
