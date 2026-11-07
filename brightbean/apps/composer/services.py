"""Composer service helpers.

Where reusable "build me a post" logic lives so it can be called from the
HTMX-driven composer view *and* from the Agent API without duplicating
state-machine semantics.

The Phase 1 helper ``sync_post_scheduled_at`` keeps ``Post.scheduled_at``
in sync with the earliest per-platform ``PlatformPost.scheduled_at``;
the Phase 2 additions wrap ``Post`` + ``PlatformPost`` creation and
status transitions so audit + idempotency live in one place.
"""

from __future__ import annotations

import copy
import datetime as dt
import uuid
from collections.abc import Iterable
from typing import Any

from django.apps import apps
from django.core.exceptions import ValidationError
from django.db import transaction


class ChannelRemovalError(ValidationError):
    """A channel copy contains work that must survive channel selection changes."""

    def __init__(self, variants):
        self.variants = variants
        super().__init__("A channel with caption history, video or thumbnails cannot be removed.")


def channel_removal_blocker(variant):
    """Shared preflight for every channel-removal entry point."""
    if apps.is_installed("apps.jrc_texts") and variant.jrc_texts.exists():
        return "caption history"
    if apps.is_installed("apps.jrc_video"):
        from apps.jrc_video.models import VideoAsset

        if VideoAsset.objects.filter(platform_post=variant).exists():
            return "video"
    # JR Content: upraveno 2026-11-04 (blok P1-B10), AGPL-3.0: nahrané náhledy kanálu se nemažou.
    if apps.is_installed("apps.jrc_thumbnails") and variant.jrc_thumbnails.exists():
        return "thumbnail"
    return None


# JR Content: přidáno 2026-11-07 (blok P1-B37), AGPL-3.0.
def lock_copies(post_ids, copy_ids=None):
    """Lock content and its channel copies in the one order every writer of copy state uses (P1-B37).

    The order is the publisher's: the ``Post`` rows first (by primary key), then their
    ``PlatformPost`` rows (by primary key). Every path that changes a copy's state or deletes it
    (edits returning it to review, taking it out of the plan, bulk actions, deletion, the
    publisher claiming it) takes these locks before it reads the state it decides on, so a copy
    the publisher has just claimed (``publishing``) is never overwritten, and a copy an edit has
    just sent back to review is never claimed. Must run inside a transaction. Returns the locked
    copies (all copies of ``post_ids``, or only ``copy_ids``) with their current state.
    """
    from .models import PlatformPost, Post

    list(Post.objects.filter(pk__in=list(post_ids)).order_by("pk").select_for_update(of=("self",)).values_list("pk"))
    copies = PlatformPost.objects.filter(post_id__in=list(post_ids))
    if copy_ids is not None:
        copies = copies.filter(pk__in=list(copy_ids))
    return list(copies.order_by("pk").select_for_update(of=("self",)))


@transaction.atomic
def remove_platform_posts(variants):
    """Remove copies atomically, keeping their parents and all saved work.

    Lock parents before checking history, matching caption saves, video uploads
    and channel additions. Reevaluate the selection under those locks so a stale
    request cannot bypass the guard or remove newly protected publication rows.
    """
    from .models import Post

    parents = list(Post.objects.filter(pk__in=variants.values("post_id")).order_by("pk").select_for_update())
    # Copies after their parents, by primary key: the lock order of every copy-state writer (``lock_copies``).
    selected = list(variants.order_by("pk").select_for_update(of=("self",)))
    blocked = [variant for variant in selected if channel_removal_blocker(variant)]
    if blocked:
        raise ChannelRemovalError(blocked)
    for variant in selected:
        variant.delete()
    for post in parents:
        sync_post_scheduled_at(post)
        post.save(update_fields=["updated_at"])
    return selected


def sync_post_scheduled_at(post):
    """Keep ``Post.scheduled_at`` in sync with the earliest child time.

    Three cases:

    * Some children have a ``scheduled_at`` → set parent to the min.
    * No children have a ``scheduled_at`` AND parent has one cached →
      clear the parent so listings, calendars, and Coalesce fallbacks
      don't keep showing the post as scheduled. This branch covers the
      "agent cancels the last scheduled child" flow — without it,
      ``Post.scheduled_at`` lingered at the old value and the post
      kept appearing as scheduled in the UI.
    * No children, parent already null → no-op.

    A post that gains a committed schedule also drops any draft-stage
    ``proposed_publish_at``: this is the chokepoint the scheduling paths flow
    through (``create_post``, ``transition_platform_post`` for the REST
    ``/schedule`` route and the MCP ``schedule_draft`` tool, and the PATCH
    re-time which calls this after saving), so the suggestion and a real
    schedule never coexist. The ``scheduled_at`` aggregate logic is unchanged —
    the publisher's ``Coalesce(scheduled_at, post__scheduled_at)`` fallback
    depends on it — so only the proposal clear is added here. The one path that
    commits a child WITHOUT a ``scheduled_at`` (the composer's per-account chip
    endpoint) clears the proposal itself rather than routing through here, to
    avoid disturbing that aggregate.
    """
    times = list(post.platform_posts.exclude(scheduled_at__isnull=True).values_list("scheduled_at", flat=True))
    if not times:
        if post.scheduled_at is not None:
            post.scheduled_at = None
            post.save(update_fields=["scheduled_at", "updated_at"])
        return
    earliest = min(times)
    update_fields = []
    if post.scheduled_at != earliest:
        post.scheduled_at = earliest
        update_fields.append("scheduled_at")
    if post.proposed_publish_at is not None:
        post.proposed_publish_at = None
        update_fields.append("proposed_publish_at")
    if update_fields:
        post.save(update_fields=[*update_fields, "updated_at"])


# ---------------------------------------------------------------------------
# Phase 2 — Agent-API-facing post creation
# ---------------------------------------------------------------------------


_INITIAL_STATUSES = {"draft", "scheduled"}


def create_post(
    *,
    workspace,
    social_account=None,
    social_accounts=None,
    caption: str,
    media_asset_ids: Iterable[Any] | None = None,
    title: str = "",
    first_comment: str = "",
    internal_notes: str = "",
    scheduled_at: dt.datetime | None = None,
    proposed_publish_at: dt.datetime | None = None,
    author=None,
    status: str = "draft",
    platform_overrides: dict[Any, dict[str, str | None]] | None = None,
):
    """Create a Post with explicitly selected channel copies, or none for a draft.

    The canonical service-layer entry point for the Agent API. The
    composer's HTMX-driven ``save_post`` view will be refactored to call
    this in a follow-up; for now both code paths coexist and only the
    API uses it.

    Contract:

    * ``status`` must be one of ``draft`` or ``scheduled``. Other
      states (``approved``, ``publishing``, …) are produced only via
      explicit transitions through ``transition_platform_post``.
    * If ``status='scheduled'``, ``scheduled_at`` is required and must
      be in the future. The publisher polls every ~15 s; a past
      ``scheduled_at`` is accepted and will fire on the very next tick,
      but the caller almost certainly meant "now-ish" so we leave the
      decision to them.
    * ``proposed_publish_at`` is an optional draft-stage suggestion. It is
      stored as-is regardless of ``status`` and carries no future-time
      requirement — it never reaches the publisher.
    * ``social_account.workspace_id`` must equal ``workspace.id``. The
      caller (auth class / view) already enforced scope, but we
      double-check here so anyone reaching the service directly can't
      bypass it.
    * Media assets are attached in order by their position in
      ``media_asset_ids``. They must already live in the workspace's
      media library.

    ``social_account`` preserves single-account callers; ``social_accounts`` selects zero or more.
    Returns the persisted Post with exactly those PlatformPost children.
    """
    from django.db import transaction

    from apps.composer.models import Post, PostMedia
    from apps.media_library.models import MediaAsset

    if status not in _INITIAL_STATUSES:
        raise ValueError(
            f"create_post can only initialise status in {sorted(_INITIAL_STATUSES)}; "
            f"got {status!r}. Use transition_platform_post for other states."
        )
    if status == "scheduled" and scheduled_at is None:
        raise ValueError("status='scheduled' requires scheduled_at.")
    accounts = list(social_accounts) if social_accounts is not None else ([social_account] if social_account else [])
    if len({account.pk for account in accounts}) != len(accounts):
        raise ValueError("Choose each channel only once.")
    if not accounts and status != "draft":
        raise ValueError("Add a channel before scheduling content.")
    for account in accounts:
        if account.workspace_id != workspace.id:
            raise ValueError(f"SocialAccount {account.id} is not in workspace {workspace.id}.")
        if getattr(account, "needs_reconnect", False):
            raise ValueError(
                f"SocialAccount {account.id} is in connection_status "
                f"{account.connection_status!r}; reconnect it before scheduling."
            )

    if status == "scheduled":
        # New copies start as drafts: the same rule as scheduling an existing draft (P1-B27).
        require_release_allowed(workspace, "draft")

    media_ids = list(media_asset_ids or [])
    # Pull and validate all referenced assets in a single query — fail
    # closed if any ID is missing or belongs to a different workspace.
    #
    # Normalize every id to a ``UUID`` for keying/lookup so all caller shapes
    # match identically: UUID objects (REST, coerced by Pydantic) and UUID
    # *strings* in any form (MCP, off the JSON-RPC wire — including uppercase or
    # braced). A raw string never compares equal to a UUID key, so without this
    # MCP-supplied assets are wrongly rejected as "not found in workspace".
    asset_map: dict[uuid.UUID, MediaAsset] = {}
    resolved: list[tuple[Any, uuid.UUID | None]] = []
    if media_ids:

        def _to_uuid(value):
            try:
                return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
            except (ValueError, TypeError, AttributeError):
                return None

        resolved = [(mid, _to_uuid(mid)) for mid in media_ids]
        found = list(MediaAsset.objects.filter(id__in=[u for _, u in resolved if u is not None], workspace=workspace))
        asset_map = {a.id: a for a in found}
        missing = [mid for mid, u in resolved if u is None or u not in asset_map]
        if missing:
            raise ValueError(f"Media asset(s) not found in workspace {workspace.id}: {missing}")

    if status == "scheduled":
        for account in accounts:
            reason = publish_blocker(account, media_types=usable_media_types(asset_map.values()))
            if reason:
                raise ValueError(reason)

    with transaction.atomic():
        post = Post.objects.create(
            workspace=workspace,
            author=author,
            title=title,
            caption=caption,
            first_comment=first_comment,
            internal_notes=internal_notes,
            scheduled_at=scheduled_at if status == "scheduled" else None,
            # A proposal is plain draft metadata — keep it independent of
            # ``status`` (unlike ``scheduled_at``, which only exists once the
            # post is committed to the publisher).
            proposed_publish_at=proposed_publish_at,
        )
        # Each override field is independent: ``None`` (or omitted)
        # means "no platform-specific override; fall back to the post
        # value via ``PlatformPost.effective_*``". Any string —
        # including ``""`` — is treated as an explicit override.
        for account in accounts:
            add_platform_post(
                post,
                account,
                status=status,
                scheduled_at=scheduled_at if status == "scheduled" else None,
                override=(platform_overrides or {}).get(account.id) or {},
            )
        for position, (_mid, u) in enumerate(resolved):
            # ``u`` is validated non-None and present in ``asset_map`` above
            # (the ``missing`` check raises otherwise); narrow for mypy.
            assert u is not None
            PostMedia.objects.create(
                post=post,
                media_asset=asset_map[u],
                position=position,
            )

    # Keep the aggregate column consistent — the publisher polls
    # PlatformPost directly so this is just for listings/UI, but the
    # composer view code relies on it.
    sync_post_scheduled_at(post)
    return post


def add_platform_post(post, account, *, status="draft", scheduled_at=None, override=None):
    """Create one explicitly selected copy; never infer a client's other channels."""
    from apps.composer.models import PlatformPost

    if account.workspace_id != post.workspace_id:
        raise ValueError("Choose a channel of this client.")
    override = override or {}
    return PlatformPost.objects.create(
        post=post,
        social_account=account,
        status=status,
        scheduled_at=scheduled_at,
        platform_specific_title=override.get("title"),
        platform_specific_caption=override.get("caption"),
        platform_specific_first_comment=override.get("first_comment"),
    )


def transition_platform_post(
    platform_post,
    target_status: str,
    *,
    scheduled_at: dt.datetime | None = None,
):
    """Apply a state-machine transition and persist.

    Wraps ``PlatformPost.transition_to`` (which only mutates the model
    in memory) with the persistence + side-effects the API needs:

    * Save the new status.
    * On ``scheduled``, set ``scheduled_at`` if provided, and sync the
      aggregate ``Post.scheduled_at`` column.
    * On ``draft``, clear ``scheduled_at`` so the row stops being a
      target for the publisher's polling query.

    Raises ``ValueError`` (from ``transition_to``) if the transition
    isn't in ``VALID_TRANSITIONS``.
    """
    from django.db import transaction

    from apps.composer.models import PlatformPost

    # Approval workflow gate — Codex review (PR #53) flagged that the
    # ``POST /posts/{id}/schedule`` route reached this function with
    # ``target_status="scheduled"`` and bypassed the approval check
    # ``create_post`` enforces. The gate belongs here so EVERY path that
    # promotes a draft to scheduled (REST `/schedule` route, any future
    # MCP transition tool, the composer's HTMX `transition_platform_post`
    # view) is covered.
    with transaction.atomic():
        # P1-B37: decide on the state under the lock, never on a stale copy: the publisher may have
        # claimed it (``publishing``) or an edit sent it back to review since it was read.
        (current,) = lock_copies([platform_post.post_id], [platform_post.pk]) or [platform_post]
        platform_post.status = current.status
        if target_status in ("scheduled", "publishing"):
            require_release_allowed(platform_post.post.workspace, platform_post.status)
            require_publishable(platform_post)
        platform_post.transition_to(target_status)
        # Always include ``updated_at`` — Django docs: when ``update_fields``
        # is explicit, ``auto_now`` fields are NOT touched unless listed.
        # Without this every API/MCP-driven transition would leave
        # ``PlatformPost.updated_at`` frozen at creation time, breaking
        # any "modified since X" sync and ORDER BY updated_at view.
        # TRANSITION_FIELDS covers everything transition_to writes (the retry
        # budget and the publish handle, not just status), so the REST/MCP
        # scheduling path persists the same reset the composer does.
        update_fields = {*PlatformPost.TRANSITION_FIELDS, "updated_at"}
        if target_status == "scheduled":
            if scheduled_at is not None:
                platform_post.scheduled_at = scheduled_at
                update_fields.add("scheduled_at")
        elif target_status == "draft":
            if platform_post.scheduled_at is not None:
                platform_post.scheduled_at = None
                update_fields.add("scheduled_at")
            # A drafted child is no longer queued — drop its QueueEntry so the
            # slot frees as a gap. Covers cancel / unschedule paths that would
            # otherwise orphan the row with a stale assigned_slot_datetime.
            from apps.calendar.models import QueueEntry

            QueueEntry.objects.filter(
                post=platform_post.post,
                queue__social_account=platform_post.social_account,
            ).delete()
        elif target_status == "published":
            update_fields.add("published_at")  # set by transition_to
        platform_post.save(update_fields=list(update_fields))

    sync_post_scheduled_at(platform_post.post)
    return platform_post


# P1-B32: channel copies a "schedule this existing content" request plans (REST ``/posts/{id}/schedule``,
# MCP ``schedule_draft``): those not yet planned that may move straight to ``scheduled``. Drafts and
# approved copies alike, as in the composer and on the board; whether one may go out in this workspace
# is ``require_release_allowed`` in ``transition_platform_post``. Copies in review, sent back or on hold
# stay where they are.
PLANNABLE_STATUSES = frozenset({"draft", "approved"})


def copies_to_schedule(post):
    """The channel copies of ``post`` a schedule request moves to ``scheduled`` (P1-B32)."""
    return [pp for pp in post.platform_posts.all() if pp.status in PLANNABLE_STATUSES]


def clone_post(post, *, author=None):
    """Create a fresh DRAFT duplicate of *post* (Clone / Repost).

    Copies caption, title, first comment, internal notes, tags, category, every
    per-platform override (``platform_specific_*`` + ``platform_extra``) and all
    media attachments. The copy starts as a ``draft`` with no schedule, so a
    published (or any) post can be re-edited and re-queued without touching the
    original. ``author`` defaults to the source author.
    """
    from django.db import transaction

    from apps.composer.models import PlatformPost, Post, PostMedia
    from apps.social_accounts.youtube_compliance import authored_youtube_settings

    with transaction.atomic():
        new_post = Post.objects.create(
            workspace=post.workspace,
            author=author or post.author,
            title=(f"Copy of {post.title}" if post.title else ""),
            caption=post.caption,
            first_comment=post.first_comment,
            internal_notes=post.internal_notes,
            tags=post.tags,
            category=post.category,
        )
        children = list(post.platform_posts.all())
        if children:
            PlatformPost.objects.bulk_create(
                [
                    PlatformPost(
                        post=new_post,
                        social_account=pp.social_account,
                        status="draft",
                        platform_specific_title=pp.platform_specific_title,
                        platform_specific_caption=pp.platform_specific_caption,
                        platform_specific_first_comment=pp.platform_specific_first_comment,
                        platform_specific_media=copy.deepcopy(pp.platform_specific_media),
                        platform_extra=copy.deepcopy(
                            authored_youtube_settings(pp.platform_extra)
                            if pp.social_account.platform == "youtube"
                            else pp.platform_extra or {}
                        ),
                    )
                    for pp in children
                ]
            )
        media = list(post.media_attachments.all())
        if media:
            PostMedia.objects.bulk_create(
                [
                    PostMedia(
                        post=new_post,
                        media_asset=pm.media_asset,
                        position=pm.position,
                        alt_text=pm.alt_text,
                        platform_overrides=copy.deepcopy(pm.platform_overrides),
                    )
                    for pm in media
                ]
            )
    return new_post


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def require_release_allowed(workspace, status) -> None:
    """Raise ``ValueError`` if the approval workflow keeps a copy in ``status`` from going out.

    The one rule (``apps.members.permissions.release_blocker``, P1-B27) for every path that
    schedules or publishes: composer, calendar, CSV import, REST and MCP. "No approvals" and
    "Optional approvals" let a draft go out directly; the required modes only after approval,
    so an approved copy may be scheduled in every mode.
    """
    from apps.members.permissions import release_blocker

    reason = release_blocker(workspace, status)
    if reason:
        raise ValueError(f"This content cannot be scheduled or published yet. {reason}")


def usable_media_types(assets) -> set[str]:
    """The media types among ``assets`` the publisher will actually upload.

    It skips an attachment whose asset has no stored file, so one of those
    can't satisfy a platform that needs media — or a video.
    """
    return {asset.media_type for asset in assets if asset.file}


def _usable_attachments():
    """``PostMedia`` rows ``usable_media_types`` would count, as a queryset."""
    from apps.composer.models import PostMedia

    return PostMedia.objects.exclude(media_asset__file="")


def post_media_types(post, *, excluding=None) -> set[str]:
    """``usable_media_types`` for ``post``'s attachments, in one query.

    ``excluding`` (a ``PostMedia``) answers for the post as it would be once
    that attachment is removed.
    """
    attachments = _usable_attachments().filter(post_id=post.pk)
    if excluding is not None:
        attachments = attachments.exclude(pk=excluding.pk)
    return set(attachments.values_list("media_asset__media_type", flat=True).distinct())


def media_types_by_post(post_ids) -> dict:
    """``post_media_types`` for many posts in one query; absent ids have none."""
    by_post: dict = {}
    rows = _usable_attachments().filter(post_id__in=post_ids).values_list("post_id", "media_asset__media_type")
    for post_id, media_type in rows.distinct():
        by_post.setdefault(post_id, set()).add(media_type)
    return by_post


def media_blocker(social_account, *, media_types) -> str:
    """Why ``social_account`` can't publish a post with these media, or "".

    TikTok and YouTube publish only video; Instagram and Pinterest need an
    image or a video. Their providers refuse anything else before calling out.
    """
    from apps.media_library.models import MediaAsset
    from providers import is_video_only, requires_media

    platform = social_account.get_platform_display()
    if is_video_only(social_account.platform) and MediaAsset.MediaType.VIDEO not in media_types:
        return f"{platform} can only publish videos. Add a video to this post before scheduling it."
    if requires_media(social_account.platform) and not media_types:
        return f"{platform} needs an image or a video. Add one to this post before scheduling it."
    return ""


def publish_blocker(social_account, *, media_types, platform_extra: dict | None = None) -> str:
    """Why a post can't publish to ``social_account`` as it stands, or "".

    The one statement of this rule. The publisher checks it before calling a
    platform, and every path that schedules a post checks it first so the user
    hears about it then rather than after the post's other platforms have gone
    out. Only failures that are certain before the platform is ever called: the
    media a platform can't do without (``media_blocker``), and a pin with no
    board, which our own Pinterest provider refuses.
    """
    reason = media_blocker(social_account, media_types=media_types)
    if reason:
        return reason
    if social_account.platform == "pinterest" and not (platform_extra or {}).get("board_id"):
        return "Pinterest needs a board for every pin. Choose one for this post in the composer before scheduling it."
    return ""


def require_publishable(platform_post, *, media_types=None) -> None:
    """Raise ``ValueError`` if ``platform_post`` is certain to fail at publish time.

    ``media_types`` lets a caller checking many rows answer it for all of them
    in one query (``media_types_by_post``) instead of one per row.
    """
    if media_types is None:
        media_types = post_media_types(platform_post.post)
    reason = publish_blocker(
        platform_post.social_account,
        media_types=media_types,
        platform_extra=platform_post.platform_extra,
    )
    if reason:
        raise ValueError(reason)


def media_change_blocker(post, *, media_types_after) -> str:
    """Why a media change would strand a scheduled row, or "".

    Scheduling checks the post as it stood then; this stops the media a
    platform needs being taken away afterwards — the video from a TikTok post,
    the last image from a pin — which would leave the row queued to fail. Only
    rows already ``scheduled`` count; anything else is checked again when it is
    scheduled.
    """
    from apps.composer.models import PlatformPost
    from providers import is_video_only

    children = post.platform_posts.filter(status=PlatformPost.Status.SCHEDULED).select_related("social_account")
    for child in children:
        if media_blocker(child.social_account, media_types=media_types_after):
            platform = child.social_account.get_platform_display()
            need = "a video" if is_video_only(child.social_account.platform) else "an image or a video"
            return (
                f"This post is scheduled to {platform}, which needs {need} on it. "
                f"Keep one on the post, or unschedule it for {platform} first."
            )
    return ""
