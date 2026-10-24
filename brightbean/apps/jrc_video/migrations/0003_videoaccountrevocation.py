from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("jrc_video", "0002_videoaccountsyncrequest")]

    operations = [
        migrations.CreateModel(
            name="VideoAccountRevocation",
            fields=[
                ("source_user_id", models.UUIDField(primary_key=True, serialize=False)),
                ("requested_at", models.DateTimeField(auto_now_add=True)),
                ("last_error", models.CharField(blank=True, max_length=255)),
            ],
        ),
    ]
