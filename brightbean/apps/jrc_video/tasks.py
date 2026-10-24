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


REFRESH_INTERVAL_SECONDS = 30


@background(schedule=0)
def refresh_video_statuses():
    """F5-B08: conversion states reach Brightbean without anybody keeping the Video tab open."""
    if not getattr(settings, "JRC_FF_SYNC_ENABLED", False):
        return
    from .uploads import refresh_all_unfinished

    try:
        refresh_all_unfinished()
    except Exception:  # noqa: BLE001 - keep the recurring job alive during an outage
        logger.warning("Video status refresh failed; retrying on the next worker cycle.", exc_info=True)
