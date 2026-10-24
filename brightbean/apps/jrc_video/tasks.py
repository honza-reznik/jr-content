"""Retry durable synchronization markers in the existing Brightbean worker."""

import logging

from background_task import background
from django.conf import settings
from django.core.management import call_command
from django.core.management.base import CommandError

logger = logging.getLogger(__name__)
SYNC_INTERVAL_SECONDS = 60


@background(schedule=0)
def retry_freeframe_sync():
    if not getattr(settings, "JRC_FF_SYNC_ENABLED", False):
        return
    try:
        call_command("jrc_sync_freeframe", pending=True)
    except CommandError:
        # Return normally so the recurring job keeps its minute cadence during
        # an outage instead of exhausting the queue library's retry limit.
        logger.warning("FreeFrame synchronization is pending; retrying on the next worker cycle.")
