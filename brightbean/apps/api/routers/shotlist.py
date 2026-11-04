# JR Content: přidáno 2026-11-04 (blok P1-B09), AGPL-3.0.
"""``/api/v1/posts/{id}/shotlist``: the shotlist of one content (what to shoot or create).

The REST twin of the Shotlist tab, using the same services (``apps.jrc_production.shotlist``):
every member reads; owner, manager, editor and contributor (own content) write, narrowed by the
key's permissions like the other content routes. Every write returns the whole list, so a caller
always sees the new order and the progress.
"""

from __future__ import annotations

import uuid

from django.db import transaction
from ninja import Field, Router, Schema
from ninja.errors import HttpError

from apps.api.limits import enforce_http_rate_limits
from apps.api.middleware import log_audit_entry
from apps.api.routers.content import _http_errors, _iso, _membership, _post, _require_write_key
from apps.jrc_production import shotlist as shotlist_services
from apps.jrc_production.models import ShotlistItem

router = Router(tags=["content"])


class ShotlistItemInfo(Schema):
    id: uuid.UUID
    kind: str = Field(description="item, or thumbnail for the automatic 'Thumbnail photo' (cannot be deleted).")
    text: str
    note: str
    is_done: bool
    position: int = Field(description="0-based place in the list.")
    updated_by: str | None = Field(description="Who changed the item last; null when nobody has yet.")
    updated_at: str | None


class ShotlistResponse(Schema):
    post_id: uuid.UUID
    can_edit: bool = Field(description="Whether this key may add, change, check off, move or delete items.")
    done: int
    total: int
    item: ShotlistItemInfo | None = Field(None, description="The item the write changed; null on read and delete.")
    items: list[ShotlistItemInfo] = Field(description="All items in their order.")


class ShotlistItemCreate(Schema):
    text: str = Field(description="What needs to be shot or created.", min_length=1, max_length=300)
    note: str = Field("", description="Optional note.", max_length=1000)


class ShotlistItemUpdate(Schema):
    text: str | None = Field(None, description="New text. The thumbnail item cannot be renamed.", max_length=300)
    note: str | None = Field(None, description="New note; an empty string clears it.", max_length=1000)
    is_done: bool | None = Field(None, description="Check off (true) or uncheck (false).")
    position: int | None = Field(None, ge=0, description="Move to this 0-based place in the list.")


def _info(item: ShotlistItem) -> ShotlistItemInfo:
    return ShotlistItemInfo(
        id=item.pk,
        kind=item.kind,
        text=item.text,
        note=item.note,
        is_done=item.is_done,
        position=item.position,
        updated_by=(item.updated_by.display_name or item.updated_by.email) if item.updated_by else None,
        updated_at=_iso(item.updated_at) if item.updated_by else None,
    )


def _response(post, membership, item_id=None) -> ShotlistResponse:
    shotlist_services.ensure_thumbnail_item(post)
    items = shotlist_services.items_for(post)
    done, total = shotlist_services.progress(items)
    infos = [_info(item) for item in items]
    return ShotlistResponse(
        post_id=post.pk,
        can_edit=shotlist_services.can_edit_shotlist(post, membership),
        done=done,
        total=total,
        item=next((info for info in infos if info.id == item_id), None),
        items=infos,
    )


def _writer(request, post_id):
    """Lock the content, then check the key's channels and the member's rights.

    Call inside ``transaction.atomic()`` that also covers the change and the response: a
    concurrent channel change waits, so the allowlist seen here holds until the commit.
    """
    post = _post(request, post_id, lock=True)
    membership = _membership(request, post)
    if not shotlist_services.can_edit_shotlist(post, membership):
        raise HttpError(403, "You cannot change the shotlist of this content.")
    return post, membership


def _not_found(exc):
    return HttpError(404, str(exc))


@router.get(
    "/{post_id}/shotlist",
    response=ShotlistResponse,
    operation_id="get_shotlist",
    summary="Read the shotlist (items, order and progress)",
)
def shotlist_get(request, post_id: uuid.UUID):
    """Every member of the workspace may read, clients and viewers included."""
    enforce_http_rate_limits(request, is_write=False)
    post = _post(request, post_id)
    membership = _membership(request, post)
    result = _response(post, membership)
    log_audit_entry(request, action="shotlist.read", target_id=post.id, status_code=200)
    return result


@router.post(
    "/{post_id}/shotlist",
    response={201: ShotlistResponse},
    operation_id="add_shotlist_item",
    summary="Add an item to the end of the shotlist",
)
def shotlist_add(request, post_id: uuid.UUID, payload: ShotlistItemCreate):
    """Writers are owner, manager, editor and (own content only) contributor. 422 for an empty or too long text."""
    enforce_http_rate_limits(request, is_write=True)
    _require_write_key(request)
    with transaction.atomic():
        post, membership = _writer(request, post_id)
        with _http_errors():
            item = shotlist_services.add_item(post, request.user, payload.text, payload.note, membership=membership)
        result = _response(post, membership, item.pk)
    log_audit_entry(request, action="shotlist.create", target_id=item.id, status_code=201)
    return 201, result


@router.patch(
    "/{post_id}/shotlist/{item_id}",
    response=ShotlistResponse,
    operation_id="update_shotlist_item",
    summary="Change, check off or move a shotlist item",
)
def shotlist_update(request, post_id: uuid.UUID, item_id: uuid.UUID, payload: ShotlistItemUpdate):
    """Send only the fields to change. 404 for an item of another content, 422 for renaming the thumbnail item."""
    enforce_http_rate_limits(request, is_write=True)
    _require_write_key(request)
    with transaction.atomic():
        post, membership = _writer(request, post_id)
        try:
            with _http_errors():
                item = shotlist_services.update_item(
                    post,
                    item_id,
                    request.user,
                    text=payload.text,
                    note=payload.note,
                    is_done=payload.is_done,
                    membership=membership,
                )
                if payload.position is not None:
                    item = shotlist_services.move_item(
                        post, item_id, request.user, index=payload.position, membership=membership
                    )
        except shotlist_services.ItemNotFoundError as exc:
            raise _not_found(exc) from exc
        result = _response(post, membership, item.pk)
    log_audit_entry(request, action="shotlist.update", target_id=item.id, status_code=200)
    return result


@router.delete(
    "/{post_id}/shotlist/{item_id}",
    response=ShotlistResponse,
    operation_id="delete_shotlist_item",
    summary="Delete a shotlist item (not the thumbnail item)",
)
def shotlist_delete(request, post_id: uuid.UUID, item_id: uuid.UUID):
    """The automatic thumbnail item cannot be deleted (422); check it off instead."""
    enforce_http_rate_limits(request, is_write=True)
    _require_write_key(request)
    with transaction.atomic():
        post, membership = _writer(request, post_id)
        try:
            with _http_errors():
                shotlist_services.delete_item(post, item_id, request.user, membership=membership)
        except shotlist_services.ItemNotFoundError as exc:
            raise _not_found(exc) from exc
        result = _response(post, membership)
    log_audit_entry(request, action="shotlist.delete", target_id=item_id, status_code=200)
    return result
