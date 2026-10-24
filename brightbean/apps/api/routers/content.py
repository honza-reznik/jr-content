# JR Content: přidáno 2026-10-24 (blok F9-B01), AGPL-3.0.
"""``/api/v1/posts/{id}/script|captions|stage|comments``: texts, stage and comments of one content.

The REST twin of the content detail page. Nothing here decides anything on its own:
scripts and captions go through ``apps.jrc_texts.services`` (versions, ``base_version``,
conflicts), the stage through ``apps.jrc_production.services.move_content`` and comments
through ``apps.approvals.comments`` (``get_visible_comments``, ``create_comment``).

Authority of a call = what the key's issuer may do in the key's workspace (the services
check that themselves, with the issuer as the actor), narrowed by the permissions the key
was granted. The narrowing is checked here with ``_KeyMembership`` before a service runs,
so a key can never do more than its issuer and never more than it was issued for.
A content outside the key's workspace, or with a channel copy outside the key's account
allowlist, is a 404 (the same rule as ``/posts/{id}``).
"""

from __future__ import annotations

import datetime as dt
import uuid
from contextlib import contextmanager

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.http import Http404, HttpRequest
from ninja import Field, Router, Schema
from ninja.errors import HttpError

from apps.api.limits import enforce_http_rate_limits
from apps.api.middleware import log_audit_entry
from apps.api.routers.posts import _get_workspace_post
from apps.approvals import comments as comment_services
from apps.composer.models import PlatformPost, Post
from apps.jrc_production.models import PipelineStage
from apps.jrc_production.services import available_moves, content_membership, move_content, pipeline_stage
from apps.jrc_texts import services as text_services
from apps.jrc_texts.content import MAX_CONTENT_BYTES
from apps.jrc_texts.models import TextDocument, TextVersion

router = Router(tags=["content"])

# A key that holds none of these is read-only for texts and stages; it could only ever
# have been used for analytics. Comments follow the UI instead (every member may comment).
_WRITE_PERMISSIONS = ("create_posts", "approve_posts")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _KeyMembership:
    """The issuer's membership with the permissions narrowed to what the key was granted.

    ``request.workspace_membership`` already holds (key grants) AND (issuer's current
    permissions). Role and ownership data stay the real ones, so the rules in
    ``can_edit_text`` and ``available_moves`` give the same answer as in the UI.
    """

    def __init__(self, membership, key_permissions):
        self.user_id = membership.user_id
        self.workspace_role = membership.workspace_role
        self.custom_role_id = membership.custom_role_id
        self.effective_permissions = {
            key: bool(value and key_permissions.get(key, False))
            for key, value in membership.effective_permissions.items()
        }


def _membership(request: HttpRequest, post) -> _KeyMembership:
    try:
        real = content_membership(post.workspace, request.user)
    except PermissionDenied as exc:
        raise HttpError(403, str(exc)) from exc
    return _KeyMembership(real, request.workspace_membership.effective_permissions)  # type: ignore[attr-defined]


def _require_write_key(request: HttpRequest) -> None:
    perms = request.workspace_membership.effective_permissions  # type: ignore[attr-defined]
    if not any(perms.get(key, False) for key in _WRITE_PERMISSIONS):
        raise HttpError(403, "Permission denied: this key is read-only for texts and stages.")


def _post(request: HttpRequest, post_id: uuid.UUID):
    post = _get_workspace_post(request, post_id)
    # The workspace relation is needed by the services; load it once.
    post.workspace = request.api_key.workspace  # type: ignore[attr-defined]
    return post


@contextmanager
def _http_errors():
    """Translate service errors into the API's error envelope."""
    try:
        yield
    except text_services.ConflictError as exc:
        raise HttpError(409, str(exc) or "Conflict.") from exc
    except PermissionDenied as exc:
        raise HttpError(403, str(exc) or "Permission denied.") from exc
    except ValidationError as exc:
        raise HttpError(422, " ".join(exc.messages)) from exc


def _iso(value: dt.datetime | None) -> str | None:
    return value.astimezone(dt.UTC).isoformat().replace("+00:00", "Z") if value else None


class VersionInfo(Schema):
    id: uuid.UUID
    number: int
    author: str = Field(description="Display name of the author, 'Former member' when the account is gone.")
    source: str = Field(description="editor, api or mcp.")
    saved_at: str | None


def _version_info(version: TextVersion) -> VersionInfo:
    return VersionInfo(
        id=version.pk,
        number=version.number,
        author=version.created_by.display_name if version.created_by else "Former member",
        source=version.source,
        saved_at=_iso(version.finalized_at),
    )


def _saved_versions(document):
    """Saved versions only: a draft is never part of this API (one person's work in progress)."""
    if document is None:
        return []
    return list(
        document.versions.filter(state=TextVersion.State.FINAL).select_related("created_by").order_by("-number")
    )


# ---------------------------------------------------------------------------
# Script
# ---------------------------------------------------------------------------


class ScriptResponse(Schema):
    post_id: uuid.UUID
    can_edit: bool = Field(description="Whether this key may write a new version.")
    version: VersionInfo | None = Field(description="The newest (or requested) saved version; null before the first.")
    markdown: str | None = Field(description="The text of `version` as Markdown.")
    versions: list[VersionInfo] = Field(description="All saved versions, newest first.")


class ScriptWrite(Schema):
    markdown: str = Field(description="The whole new script as Markdown.", max_length=MAX_CONTENT_BYTES)
    base_version: uuid.UUID | None = Field(
        None,
        description=(
            "Id of the version this text was written from. Required once the script has a version and "
            "must be the newest one (409 otherwise); omit it only for the very first version."
        ),
    )


def _script_response(post, membership, selected_id=None) -> ScriptResponse:
    document = TextDocument.objects.filter(post=post, kind=TextDocument.Kind.SCRIPT).first()
    versions = _saved_versions(document)
    shown = None
    if selected_id is not None:
        shown = next((v for v in versions if v.pk == selected_id), None)
        if shown is None:
            raise Http404()
    elif versions:
        shown = versions[0]
    return ScriptResponse(
        post_id=post.pk,
        can_edit=text_services.can_edit_text(post, membership),
        version=_version_info(shown) if shown else None,
        markdown=shown.text if shown else None,
        versions=[_version_info(v) for v in versions],
    )


@router.get(
    "/{post_id}/script",
    response=ScriptResponse,
    operation_id="get_script",
    summary="Read the script (newest saved version and the version list)",
)
def script_get(request, post_id: uuid.UUID, version: uuid.UUID | None = None):
    """Every member of the workspace may read. `?version=<id>` returns an older saved version."""
    enforce_http_rate_limits(request, is_write=False)
    post = _post(request, post_id)
    membership = _membership(request, post)
    result = _script_response(post, membership, version)
    log_audit_entry(request, action="script.read", target_id=post.id, status_code=200)
    return result


@router.post(
    "/{post_id}/script",
    response={201: ScriptResponse, 200: ScriptResponse},
    operation_id="save_script",
    summary="Save the script as a new version",
)
def script_save(request, post_id: uuid.UUID, payload: ScriptWrite):
    """Same rules as the Script tab: writers are owner, manager, editor and (own content only) contributor.

    Returns 201 with the new version, 200 when the text equals the newest version (nothing is created),
    409 when `base_version` is not the newest version or somebody has an unsaved draft in the editor,
    422 when the Markdown does not fit the editor schema.
    """
    enforce_http_rate_limits(request, is_write=True)
    _require_write_key(request)
    post = _post(request, post_id)
    membership = _membership(request, post)
    if not text_services.can_edit_text(post, membership):
        raise HttpError(403, "You cannot edit texts for this post.")
    status = 201
    with _http_errors(), transaction.atomic():
        Post.objects.select_for_update().get(pk=post.pk)
        document = TextDocument.objects.filter(post=post, kind=TextDocument.Kind.SCRIPT).first()
        if document is None:
            if payload.base_version is not None:
                raise text_services.ConflictError("The script has no saved version yet. Reload the text.")
            text_services.create_document(post.pk, request.user, markdown=payload.markdown, source="api")
        else:
            base = (
                document.versions.filter(pk=payload.base_version, state=TextVersion.State.FINAL).first()
                if payload.base_version
                else None
            )
            if base is None:
                raise text_services.ConflictError("The script changed. Reload the text and send base_version.")
            draft = text_services.edit(base.pk, request.user, source="api")
            saved = text_services.save(draft.pk, request.user, expected_revision=0, markdown=payload.markdown)
            if saved.pk == base.pk:
                status = 200
    result = _script_response(post, membership)
    log_audit_entry(request, action="script.write", target_id=post.id, status_code=status)
    return status, result


# ---------------------------------------------------------------------------
# Captions
# ---------------------------------------------------------------------------


class CaptionResponse(Schema):
    post_id: uuid.UUID
    platform_post_id: uuid.UUID = Field(description="The channel copy of the content.")
    social_account_id: uuid.UUID
    platform: str
    can_edit: bool
    char_limit: int
    char_count: int = Field(description="Length by the channel's own counting rules (emoji = 1, LinkedIn escapes).")
    text: str = Field(description="The newest saved caption, or the caption the copy has before its first version.")
    version: VersionInfo | None = Field(description="Null until the first version is saved.")
    versions: list[VersionInfo]


class CaptionWrite(Schema):
    text: str = Field(description="The whole new caption.", max_length=100_000)
    base_version: uuid.UUID | None = Field(
        None,
        description=(
            "Id of the version this caption was written from. Required once the channel has a saved version and "
            "must be the newest one (409 otherwise); omit it only for a channel without any version."
        ),
    )


def _channel_enabled(account) -> bool:
    return getattr(getattr(account, "jrc_channel", None), "is_enabled", True)


def _variant(request, post, platform_post_id: uuid.UUID) -> PlatformPost:
    """A visible channel copy of this content whose account is in the key's allowlist, else 404."""
    variant = (
        PlatformPost.objects.filter(pk=platform_post_id, post=post)
        .select_related("social_account__jrc_channel")
        .first()
    )
    allowed = {sa.id for sa in request.api_key.social_accounts.all()}
    if variant is None or variant.social_account_id not in allowed or not _channel_enabled(variant.social_account):
        raise Http404()
    return variant


def _caption_response(post, variant, membership) -> CaptionResponse:
    document = TextDocument.objects.filter(platform_post=variant, kind=TextDocument.Kind.CAPTION).first()
    versions = _saved_versions(document)
    text = versions[0].text if versions else variant.effective_caption
    account = variant.social_account
    return CaptionResponse(
        post_id=post.pk,
        platform_post_id=variant.pk,
        social_account_id=account.pk,
        platform=account.platform,
        can_edit=text_services.can_edit_text(post, membership),
        char_limit=account.char_limit,
        char_count=account.caption_wire_length(text),
        text=text,
        version=_version_info(versions[0]) if versions else None,
        versions=[_version_info(v) for v in versions],
    )


@router.get(
    "/{post_id}/captions",
    response=list[CaptionResponse],
    operation_id="list_captions",
    summary="Read the captions of all channels of a content",
)
def captions_list(request, post_id: uuid.UUID):
    """One entry per enabled channel copy, with its newest saved version and the version list."""
    enforce_http_rate_limits(request, is_write=False)
    post = _post(request, post_id)
    membership = _membership(request, post)
    variants = (
        PlatformPost.objects.filter(post=post)
        .select_related("social_account__jrc_channel")
        .order_by("social_account__account_name", "pk")
    )
    result = [_caption_response(post, v, membership) for v in variants if _channel_enabled(v.social_account)]
    log_audit_entry(request, action="caption.read", target_id=post.id, status_code=200)
    return result


@router.get(
    "/{post_id}/captions/{platform_post_id}",
    response=CaptionResponse,
    operation_id="get_caption",
    summary="Read the caption of one channel",
)
def caption_get(request, post_id: uuid.UUID, platform_post_id: uuid.UUID):
    enforce_http_rate_limits(request, is_write=False)
    post = _post(request, post_id)
    membership = _membership(request, post)
    variant = _variant(request, post, platform_post_id)
    result = _caption_response(post, variant, membership)
    log_audit_entry(request, action="caption.read", target_id=variant.id, status_code=200)
    return result


def _caption_write(request, post_id, platform_post_id, payload: CaptionWrite):
    enforce_http_rate_limits(request, is_write=True)
    _require_write_key(request)
    post = _post(request, post_id)
    membership = _membership(request, post)
    variant = _variant(request, post, platform_post_id)
    if not text_services.can_edit_text(post, membership):
        raise HttpError(403, "You cannot edit texts for this post.")
    text = payload.text.replace("\r\n", "\n").replace("\r", "\n")
    with _http_errors():
        _, changed = text_services.save_caption(
            post.pk,
            variant.pk,
            request.user,
            text,
            base_version_id=payload.base_version,
            source="api",
        )
    variant.refresh_from_db()
    status = 201 if changed else 200
    result = _caption_response(post, variant, membership)
    log_audit_entry(request, action="caption.write", target_id=variant.id, status_code=status)
    return status, result


_CAPTION_DOC = """A write is always a new version, copied to the channel's caption field; history is never overwritten.
Limit of the channel and `base_version` as on the Caption tab. 201 new version, 200 identical text (nothing created),
409 stale `base_version` or an unsaved draft of the text, 422 over the channel limit."""


@router.post(
    "/{post_id}/captions/{platform_post_id}",
    response={201: CaptionResponse, 200: CaptionResponse},
    operation_id="save_caption",
    summary="Save a channel caption as a new version",
    description=_CAPTION_DOC,
)
def caption_post(request, post_id: uuid.UUID, platform_post_id: uuid.UUID, payload: CaptionWrite):
    return _caption_write(request, post_id, platform_post_id, payload)


@router.patch(
    "/{post_id}/captions/{platform_post_id}",
    response={201: CaptionResponse, 200: CaptionResponse},
    operation_id="update_caption",
    summary="Save a channel caption as a new version (same as POST)",
    description=_CAPTION_DOC,
)
def caption_patch(request, post_id: uuid.UUID, platform_post_id: uuid.UUID, payload: CaptionWrite):
    return _caption_write(request, post_id, platform_post_id, payload)


# ---------------------------------------------------------------------------
# Production stage
# ---------------------------------------------------------------------------


class StageResponse(Schema):
    post_id: uuid.UUID
    stage: str = Field(description="Idea, script, shooting, editing, review, approved, ready or published.")
    available_moves: list[str] = Field(description="Stages this key may move the content to right now.")


class StageMove(Schema):
    stage: str = Field(description="Target stage; must be one of `available_moves`.")
    expected_stage: str = Field(description="The stage the caller saw. 409 when the content is elsewhere by now.")
    scheduled_at: dt.datetime | None = Field(
        None, description="Required for the move to `ready` (future time, timezone-aware)."
    )


def _stage_response(post, membership) -> StageResponse:
    post.refresh_from_db()
    return StageResponse(
        post_id=post.pk,
        stage=pipeline_stage(post),
        available_moves=sorted(set(available_moves(post, membership))),
    )


@router.get(
    "/{post_id}/stage",
    response=StageResponse,
    operation_id="get_stage",
    summary="Read the production stage and the moves allowed for this key",
)
def stage_get(request, post_id: uuid.UUID):
    enforce_http_rate_limits(request, is_write=False)
    post = _post(request, post_id)
    membership = _membership(request, post)
    result = _stage_response(post, membership)
    log_audit_entry(request, action="stage.read", target_id=post.id, status_code=200)
    return result


@router.post(
    "/{post_id}/stage",
    response=StageResponse,
    operation_id="move_stage",
    summary="Move the content to another production stage",
)
def stage_move(request, post_id: uuid.UUID, payload: StageMove):
    """Uses the board's `move_content`: only moves in `available_moves` for the issuer's role AND the key's
    permissions, approvals and scheduling stay with Brightbean. 403 for a move the role may not make,
    409 when `expected_stage` is stale, 422 for an invalid stage or missing/past `scheduled_at`."""
    enforce_http_rate_limits(request, is_write=True)
    _require_write_key(request)
    post = _post(request, post_id)
    membership = _membership(request, post)
    if payload.stage not in PipelineStage.values:
        raise HttpError(422, "Choose a valid stage.")
    if pipeline_stage(post) != payload.expected_stage:
        raise HttpError(409, "This content changed. Reload the stage and try again.")
    if payload.stage not in available_moves(post, membership):
        raise HttpError(403, "You cannot make this move. Check your permissions and the current stage.")
    try:
        with _http_errors():
            move_content(
                post,
                payload.stage,
                request.user,
                expected_stage=payload.expected_stage,
                scheduled_at=payload.scheduled_at,
            )
    except HttpError as exc:
        # move_content re-checks under a lock: a race with another change is a conflict, not bad input.
        post.refresh_from_db()
        if exc.status_code == 422 and pipeline_stage(post) != payload.expected_stage:
            raise HttpError(409, "This content changed. Reload the stage and try again.") from exc
        raise
    result = _stage_response(post, membership)
    log_audit_entry(request, action="stage.move", target_id=post.id, status_code=200)
    return result


# ---------------------------------------------------------------------------
# Comments
# ---------------------------------------------------------------------------


class CommentInfo(Schema):
    id: uuid.UUID
    author: str
    body: str
    visibility: str = Field(description="internal (team only) or external (the client sees it too).")
    is_resolved: bool
    created_at: str
    replies: list[CommentInfo] = Field(default_factory=list)


class CommentWrite(Schema):
    body: str = Field(description="Comment text.", min_length=1, max_length=10_000)
    visibility: str | None = Field(
        None,
        description=(
            "internal or external. Team members default to internal; clients and viewers always write external. "
            "A reply always takes the visibility of its thread."
        ),
    )
    parent_id: uuid.UUID | None = Field(None, description="Id of the thread (top-level comment) to reply to.")


def _comment_info(comment) -> CommentInfo:
    return CommentInfo(
        id=comment.pk,
        author=comment.author.display_name if comment.author else "Former member",
        body=comment.body,
        visibility=comment.visibility,
        is_resolved=comment.is_resolved,
        created_at=_iso(comment.created_at),
        replies=[_comment_info(reply) for reply in getattr(comment, "visible_replies", [])],
    )


CommentInfo.model_rebuild()


@router.get(
    "/{post_id}/comments",
    response=list[CommentInfo],
    operation_id="list_comments",
    summary="Read the comments the issuer may see",
)
def comments_list(request, post_id: uuid.UUID):
    """Threads with replies. Clients and viewers never receive internal comments or replies to them."""
    enforce_http_rate_limits(request, is_write=False)
    post = _post(request, post_id)
    result = [_comment_info(c) for c in comment_services.get_comments_for_post(post, request.user)]
    log_audit_entry(request, action="comment.read", target_id=post.id, status_code=200)
    return result


@router.post(
    "/{post_id}/comments",
    response={201: CommentInfo},
    operation_id="add_comment",
    summary="Add a comment or a reply",
)
def comment_add(request, post_id: uuid.UUID, payload: CommentWrite):
    """Every member of the workspace may comment, as in the content detail. Internal comments are team-only."""
    enforce_http_rate_limits(request, is_write=True)
    post = _post(request, post_id)
    body = payload.body.strip()
    if not body:
        raise HttpError(422, "Write a comment before sending it.")
    try:
        comment = comment_services.create_comment(
            post=post,
            author=request.user,
            body=body,
            visibility=payload.visibility,
            parent_id=payload.parent_id,
        )
    except PermissionError as exc:
        raise HttpError(403, str(exc)) from exc
    except ValueError as exc:
        if str(exc) == "Comment thread not found.":
            raise HttpError(404, str(exc)) from exc
        raise HttpError(422, str(exc)) from exc
    log_audit_entry(request, action="comment.create", target_id=comment.id, status_code=201)
    # A new comment has no replies yet; the author always sees it.
    return 201, _comment_info(comment)
