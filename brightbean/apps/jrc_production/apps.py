from django.apps import AppConfig


class JrcProductionConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.jrc_production"
    verbose_name = "JR Content production"
