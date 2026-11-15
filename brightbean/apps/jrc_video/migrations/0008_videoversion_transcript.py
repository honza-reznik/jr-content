from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("jrc_video", "0007_videosessionrevocation")]
    operations = [
        migrations.AddField(
            model_name="videoversion",
            name="transcript",
            field=models.FileField(blank=True, max_length=255, upload_to="jrc_video_transcripts/"),
        ),
        migrations.AddField(
            model_name="videoversion",
            name="transcript_filename",
            field=models.CharField(blank=True, max_length=200),
        ),
    ]
