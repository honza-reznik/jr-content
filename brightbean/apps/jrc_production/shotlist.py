# JR Content: přidáno 2026-11-04 (blok P1-B09), AGPL-3.0.
"""Shotlist of a content: what must be shot or created (P1-B09).

One set of rules for the Shotlist tab and the REST API. Every write locks the content row,
re-checks the member's rights and records added / checked / unchecked / removed items in
``ShotlistChange`` for the History tab. Every content has one automatic "Thumbnail photo"
item: it is a reminder for the shoot, checked off by hand, and cannot be deleted.
"""

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Max

from apps.composer.models import Post

from .models import ShotlistChange, ShotlistItem
from .services import content_membership

THUMBNAIL_TEXT = "Thumbnail photo"
MAX_TEXT = 300
MAX_NOTE = 1000
MAX_ITEMS = 200


class ItemNotFoundError(Exception):
    """The item is not part of this content (or was deleted meanwhile)."""


def can_edit_shotlist(post, membership):
    """Team writes: owner, manager, editor, contributor on own content. Client and viewer read only."""
    permissions = membership.effective_permissions
    return permissions.get("create_posts", False) and (
        post.author_id == membership.user_id or permissions.get("edit_others_posts", False)
    )


def ensure_thumbnail_item(post):
    """Create the automatic item if it is missing; idempotent (one per content by constraint)."""
    item, _ = ShotlistItem.objects.get_or_create(
        post_id=post.pk,
        kind=ShotlistItem.Kind.THUMBNAIL,
        defaults={"text": THUMBNAIL_TEXT, "position": 0},
    )
    return item


def items_for(post):
    return list(ShotlistItem.objects.filter(post_id=post.pk).select_related("updated_by"))


def progress(items):
    return sum(1 for item in items if item.is_done), len(items)


def _clean_text(text):
    text = " ".join((text or "").split())
    if not text:
        raise ValidationError("Write what needs to be shot or created.")
    if len(text) > MAX_TEXT:
        raise ValidationError(f"Keep the item under {MAX_TEXT} characters.")
    return text


def _clean_note(note):
    note = (note or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if len(note) > MAX_NOTE:
        raise ValidationError(f"Keep the note under {MAX_NOTE} characters.")
    return note


def _lock(post, user, membership=None):
    """Lock the content, then authorize against the member's current role.

    ``membership`` is the API's key-narrowed one, read before the lock: it must allow the
    change too, but a role lowered while the request waited still wins.
    """
    post = Post.objects.select_for_update(of=("self",)).select_related("workspace").get(pk=post.pk)
    real = content_membership(post.workspace, user)
    if not can_edit_shotlist(post, real) or (membership is not None and not can_edit_shotlist(post, membership)):
        raise PermissionDenied("You cannot change the shotlist of this content.")
    return post


def _item(post, item_id):
    try:
        return ShotlistItem.objects.select_for_update().get(pk=item_id, post_id=post.pk)
    except (ShotlistItem.DoesNotExist, ValueError, TypeError, ValidationError):
        raise ItemNotFoundError("This shotlist item no longer exists. Reload the page.") from None


def _log(post, item, action, user):
    ShotlistChange.objects.create(post=post, item_id=item.pk, item_text=item.text, action=action, user=user)


@transaction.atomic
def add_item(post, user, text, note="", *, membership=None):
    post = _lock(post, user, membership)
    text, note = _clean_text(text), _clean_note(note)
    ensure_thumbnail_item(post)
    existing = ShotlistItem.objects.filter(post_id=post.pk)
    if existing.count() >= MAX_ITEMS:
        raise ValidationError(f"A shotlist can have at most {MAX_ITEMS} items.")
    last = existing.aggregate(last=Max("position"))["last"]
    item = ShotlistItem.objects.create(
        post=post,
        text=text,
        note=note,
        position=0 if last is None else last + 1,
        created_by=user,
        updated_by=user,
    )
    _log(post, item, ShotlistChange.Action.ADDED, user)
    return item


@transaction.atomic
def update_item(post, item_id, user, *, text=None, note=None, is_done=None, membership=None):
    post = _lock(post, user, membership)
    item = _item(post, item_id)
    fields = []
    if text is not None:
        text = _clean_text(text)
        if item.kind == ShotlistItem.Kind.THUMBNAIL and text != item.text:
            raise ValidationError("The thumbnail photo item cannot be renamed. Add a note instead.")
        if text != item.text:
            item.text = text
            fields.append("text")
    if note is not None:
        note = _clean_note(note)
        if note != item.note:
            item.note = note
            fields.append("note")
    action = None
    if is_done is not None and bool(is_done) != item.is_done:
        item.is_done = bool(is_done)
        fields.append("is_done")
        action = ShotlistChange.Action.DONE if item.is_done else ShotlistChange.Action.UNDONE
    if fields:
        item.updated_by = user
        item.save(update_fields=[*fields, "updated_by", "updated_at"])
    if action:
        _log(post, item, action, user)
    return item


@transaction.atomic
def move_item(post, item_id, user, *, index=None, offset=None, membership=None):
    """Move an item to ``index`` (0-based) or by ``offset`` (-1 up, +1 down), clamped.

    The list is renumbered 0..n-1 without gaps, all under the content lock.
    """
    post = _lock(post, user, membership)
    item = _item(post, item_id)
    ordered = list(ShotlistItem.objects.select_for_update().filter(post_id=post.pk))
    current = next(i for i, other in enumerate(ordered) if other.pk == item.pk)
    try:
        target = current + int(offset) if offset is not None else int(index)
    except (TypeError, ValueError):
        raise ValidationError("Choose a valid position.") from None
    others = [other for other in ordered if other.pk != item.pk]
    others.insert(max(0, min(target, len(others))), item)
    changed = []
    for position, other in enumerate(others):
        if other.position != position:
            other.position = position
            changed.append(other)
    if changed:
        ShotlistItem.objects.bulk_update(changed, ["position"])
    if item in changed:
        # Only the moved item records who moved it; its neighbours just renumber.
        item.updated_by = user
        item.save(update_fields=["updated_by", "updated_at"])
    item.refresh_from_db()
    return item


@transaction.atomic
def delete_item(post, item_id, user, *, membership=None):
    post = _lock(post, user, membership)
    item = _item(post, item_id)
    if item.kind == ShotlistItem.Kind.THUMBNAIL:
        raise ValidationError("The thumbnail photo item cannot be deleted. Check it off when the photo is taken.")
    _log(post, item, ShotlistChange.Action.DELETED, user)
    item.delete()
