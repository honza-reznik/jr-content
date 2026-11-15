# JR Content: přidáno 2026-11-15 (blok P2-FABLE), AGPL-3.0.
"""Remove a thumbnail's stored files once its row is gone (permanent deletion of content).

``ChannelThumbnail`` rows go with their content (``on_delete=CASCADE`` from ``Post``), which is what
emptying the Trash does; without this the originals and small previews stayed in the ``jrc-app``
bucket for good (nobody could read them, the file gateway needs the row, but they took space).
Same pattern as ``jrc_video.signals.transcript_deleted``: the objects are deleted only after the
transaction commits, so a rolled-back deletion keeps its files.
"""

import logging

from django.db import transaction
from django.db.models.signals import post_delete
from django.dispatch import receiver

from .models import ChannelThumbnail

logger = logging.getLogger(__name__)


def delete_file(storage, name):
    try:
        storage.delete(name)
    except Exception:  # noqa: BLE001 - a committed deletion must not fail because of a leftover file
        logger.exception("Could not remove stored thumbnail file %s", name)


@receiver(post_delete, sender=ChannelThumbnail)
def thumbnail_deleted(sender, instance, using, **kwargs):
    for field in (instance.image, instance.small_image):
        if field:
            storage, name = field.storage, field.name
            transaction.on_commit(lambda storage=storage, name=name: delete_file(storage, name), using=using)
