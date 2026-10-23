import logging

from django.apps import AppConfig
from django.conf import settings

logger = logging.getLogger(__name__)


def warn_if_sync_disabled():
    """Startup warning when credentials are missing (never logs their values)."""
    if hasattr(settings, "JRC_FF_API_URL") and not (
        getattr(settings, "JRC_FF_SYNC_EMAIL", "") and getattr(settings, "JRC_FF_SYNC_PASSWORD", "")
    ):
        logger.warning(
            "FreeFrame synchronization is disabled: JRC_FF_SYNC_EMAIL or JRC_FF_SYNC_PASSWORD is missing "
            "(run make env). Accounts, projects and memberships are not synchronized."
        )
        return True
    return False


class JrcVideoConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.jrc_video"
    verbose_name = "JR Content video"

    def ready(self):
        from . import signals  # noqa: F401

        warn_if_sync_disabled()
