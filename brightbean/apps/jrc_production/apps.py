from django.apps import AppConfig


class JrcProductionConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.jrc_production"
    verbose_name = "JR Content production"

    def ready(self):
        from django.db.models.signals import post_save

        from apps.composer.models import Post

        from .signals import add_thumbnail_item

        # Every new content gets its "Thumbnail photo" shotlist item, whichever path created it (P1-B09).
        post_save.connect(add_thumbnail_item, sender=Post, dispatch_uid="jrc_shotlist_thumbnail")
