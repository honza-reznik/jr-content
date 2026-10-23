from django.db import migrations


def hide_custom(apps, schema_editor):
    apps.get_model("social_accounts", "PlatformVisibility").objects.using(
        schema_editor.connection.alias
    ).update_or_create(platform="custom", defaults={"is_visible": False})


def remove_custom(apps, schema_editor):
    apps.get_model("social_accounts", "PlatformVisibility").objects.using(
        schema_editor.connection.alias
    ).filter(platform="custom").delete()


class Migration(migrations.Migration):
    dependencies = [("jrc_channels", "0001_initial")]
    operations = [migrations.RunPython(hide_custom, remove_custom)]
