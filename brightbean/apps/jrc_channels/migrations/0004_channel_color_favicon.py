import apps.common.validators
import apps.jrc_channels.icons
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("jrc_channels", "0003_channelprofile_icon")]

    operations = [
        migrations.AddField(
            model_name="channelprofile",
            name="color",
            field=models.CharField(blank=True, default="", max_length=7, validators=[apps.common.validators.validate_hex_color]),
        ),
        migrations.AddField(
            model_name="channelprofile",
            name="favicon",
            field=models.ImageField(blank=True, upload_to=apps.jrc_channels.icons.icon_path),
        ),
    ]
