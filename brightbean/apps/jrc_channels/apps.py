from django.apps import AppConfig


class JrcChannelsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.jrc_channels"
    verbose_name = "JR Content channels"

    def ready(self):
        from . import signals  # noqa: F401
