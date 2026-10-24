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
        # django-background-tasks imports '<INSTALLED_APPS entry>.tasks' and
        # cannot discover an entry naming an AppConfig class. Register the
        # callable on worker startup as well as in the migration process.
        from . import signals, tasks  # noqa: F401

        warn_if_sync_disabled()
        from django.db.models.signals import post_migrate

        post_migrate.connect(self._register_tasks, sender=self)

    @staticmethod
    def _register_tasks(sender, **kwargs):
        from apps.common.background import register_recurring_task

        from .tasks import REFRESH_INTERVAL_SECONDS, SYNC_INTERVAL_SECONDS, refresh_video_statuses, retry_freeframe_sync

        register_recurring_task(
            retry_freeframe_sync, repeat=SYNC_INTERVAL_SECONDS, verbose_name="jrc_retry_freeframe_sync"
        )
        register_recurring_task(
            refresh_video_statuses, repeat=REFRESH_INTERVAL_SECONDS, verbose_name="jrc_refresh_video_statuses"
        )
