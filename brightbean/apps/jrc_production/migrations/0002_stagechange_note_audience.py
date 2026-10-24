from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("jrc_production", "0001_initial")]

    operations = [
        migrations.AddField(
            model_name="stagechange",
            name="actor_role",
            field=models.CharField(blank=True, default="", max_length=20),
        ),
        # Past roles cannot be reconstructed safely. Existing notes stay internal.
        migrations.AddField(
            model_name="stagechange",
            name="note_visibility",
            field=models.CharField(
                choices=[("internal", "Internal"), ("external", "External")], default="internal", max_length=10
            ),
        ),
    ]
