from django.apps import AppConfig


class JrcVideoConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.jrc_video"
    verbose_name = "JR Content video"

    def ready(self):
        from . import signals  # noqa: F401
