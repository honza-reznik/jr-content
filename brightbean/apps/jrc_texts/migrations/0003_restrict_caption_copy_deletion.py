from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("jrc_texts", "0002_protect_final_versions")]

    operations = [
        migrations.AlterField(
            model_name="textdocument",
            name="platform_post",
            field=models.ForeignKey(
                to="composer.platformpost",
                on_delete=models.RESTRICT,
                null=True,
                blank=True,
                related_name="jrc_texts",
            ),
        ),
    ]
