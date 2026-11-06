"""P1-B29: audit rows outlive a deleted key (SET_NULL + key prefix/name)."""

import django.db.models.deletion
from django.db import migrations, models


def fill_key_identity(apps, schema_editor):
    Audit = apps.get_model("api_keys", "ApiKeyAuditLog")
    ApiKey = apps.get_model("api_keys", "ApiKey")
    for key in ApiKey.objects.all().only("id", "lookup_prefix", "name").iterator():
        Audit.objects.filter(api_key_id=key.id, key_prefix="").update(
            key_prefix=key.lookup_prefix[:16], key_name=key.name[:100]
        )


class Migration(migrations.Migration):
    dependencies = [("api_keys", "0004_usage_hourly_and_cap_overrides")]

    operations = [
        migrations.AddField(
            model_name="apikeyauditlog",
            name="key_prefix",
            field=models.CharField(blank=True, default="", max_length=16),
        ),
        migrations.AddField(
            model_name="apikeyauditlog",
            name="key_name",
            field=models.CharField(blank=True, default="", max_length=100),
        ),
        migrations.AlterField(
            model_name="apikeyauditlog",
            name="api_key",
            field=models.ForeignKey(
                blank=True,
                help_text="Set for bb_studio_ key requests; null for OAuth callers (see actor_user) "
                "and after the key was deleted (key_prefix/key_name keep the row readable).",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="audit_logs",
                to="api_keys.apikey",
            ),
        ),
        migrations.RunPython(fill_key_identity, migrations.RunPython.noop),
    ]
