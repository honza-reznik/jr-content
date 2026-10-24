from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("approvals", "0004_postcomment_is_resolved_alter_postcomment_visibility")]

    operations = [
        migrations.AddField(
            model_name="approvalaction",
            name="actor_role",
            field=models.CharField(blank=True, default="", max_length=20),
        ),
        # Past roles cannot be reconstructed safely. Existing notes stay internal.
        migrations.AddField(
            model_name="approvalaction",
            name="note_visibility",
            field=models.CharField(
                choices=[("internal", "Internal"), ("external", "External")], default="internal", max_length=10
            ),
        ),
    ]
