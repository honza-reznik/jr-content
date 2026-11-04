# JR Content: přidáno 2026-11-04 (blok P1-B09), AGPL-3.0.


def add_thumbnail_item(sender, instance, created, raw=False, **kwargs):
    """New content (board, composer, REST, duplicate) starts with the automatic thumbnail item."""
    if not created or raw:
        return
    from .shotlist import ensure_thumbnail_item

    ensure_thumbnail_item(instance)
