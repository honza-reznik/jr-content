"""Version operations. All writers lock the document before accessing its drafts.

ConflictError.status_code is the HTTP status for the F4/F9 adapters. No service
implicitly resumes a draft: mutation requires its UUID and revision. Administrators
may discard an orphan, including when starting a new edit from a saved version.
"""

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from apps.approvals.edits import return_edited_to_review, reviewed_content
from apps.composer.models import PlatformPost, Post
from apps.jrc_production.notifications import notify_text_version
from apps.jrc_production.services import content_membership
from apps.members.models import WorkspaceMembership

from .content import normalize_content
from .models import TextDocument, TextVersion


class ConflictError(Exception):
    status_code = 409


class CaptionSave(tuple):
    """``(version, changed)`` of ``save_caption``; ``returned`` lists the copies the save sent back to
    review (``apps.approvals.edits``, P1-B37), for the notice the caller shows."""

    def __new__(cls, version, changed, returned):
        result = super().__new__(cls, (version, changed))
        result.returned = returned
        return result


def can_edit_text(post, membership):
    permissions = membership.effective_permissions
    return permissions.get("create_posts", False) and (
        post.author_id == membership.user_id or permissions.get("edit_others_posts", False)
    )


def _authorize(document, user, *, write=False):
    membership = content_membership(document.post.workspace, user)
    if write and not can_edit_text(document.post, membership):
        raise PermissionDenied("You cannot edit texts for this post.")


def can_recover_orphan(draft, user):
    """Only workspace administrators may remove a draft whose owner has left."""
    if (
        draft.draft_owner_id is not None
        and WorkspaceMembership.objects.filter(
            workspace_id=draft.document.post.workspace_id, user_id=draft.draft_owner_id
        ).exists()
    ):
        return False
    membership = content_membership(draft.document.post.workspace, user)
    return (
        membership.custom_role_id is None
        and membership.workspace_role
        in {WorkspaceMembership.WorkspaceRole.OWNER, WorkspaceMembership.WorkspaceRole.MANAGER}
    ) or membership.effective_permissions.get("manage_workspace_settings", False)


def _document(document_id, user, *, write=False):
    query = TextDocument.objects.select_related("post__workspace", "platform_post")
    if write:
        query = query.select_for_update(of=("self",))
    document = query.get(pk=document_id)
    _authorize(document, user, write=write)
    return document


def get_version(version_id, user):
    version = TextVersion.objects.select_related("document__post__workspace").get(pk=version_id)
    _authorize(version.document, user)
    if version.state == TextVersion.State.DRAFT:
        _authorize(version.document, user, write=True)
        if version.draft_owner_id != user.pk:
            raise PermissionDenied("This draft belongs to another editor.")
    return version


def latest_version(document_id, user):
    document = _document(document_id, user)
    return document.versions.filter(state=TextVersion.State.FINAL).order_by("-number").first()


@transaction.atomic
def create_document(
    post_id,
    user,
    *,
    kind="script",
    platform_post_id=None,
    format="rich",
    content=None,
    markdown=None,
    source="editor",
    initial_draft=False,
):
    post = Post.objects.select_for_update().select_related("workspace").get(pk=post_id)
    # Authorize before validating any channel-specific data.
    probe = TextDocument(post=post)
    _authorize(probe, user, write=True)
    document = TextDocument(
        post=post, workspace=post.workspace, kind=kind, platform_post_id=platform_post_id, format=format
    )
    document.full_clean()
    if source not in TextVersion.Source.values:
        raise ValidationError("Choose a valid version source.")
    normalized, text = normalize_content(content=content, markdown=markdown, plain=format == "plain")
    document.save()
    if initial_draft:
        start_draft(document.pk, user, source=source)
        return document
    version = TextVersion.objects.create(
        document=document,
        number=1,
        state=TextVersion.State.FINAL,
        source=source,
        content=normalized,
        text=text,
        created_by=user,
        finalized_at=timezone.now(),
    )
    # A script (or concept, P1-B08) created with real text is a new version for the team (F7-B03).
    # Empty saved texts and the pre-existing copy a caption history starts from
    # do not notify: the actual edit saved on top of that caption is what changes it.
    if kind in (TextDocument.Kind.SCRIPT, TextDocument.Kind.CONCEPT) and text.strip():
        notify_text_version(version, user)
    return document


@transaction.atomic
def start_draft(document_id, user, *, source="editor"):
    """Start version 1 without saving an empty placeholder; also works after discard."""
    document = _document(document_id, user, write=True)
    if document.versions.filter(state=TextVersion.State.FINAL).exists():
        raise ConflictError("This text has a saved version. Reload the text.")
    draft = document.versions.filter(state=TextVersion.State.DRAFT).first()
    if draft is not None and not can_recover_orphan(draft, user):
        raise ConflictError("This text already has a draft.")
    if source not in TextVersion.Source.values:
        raise ValidationError("Choose a valid version source.")
    if draft is not None:
        draft.delete()
    content, text = normalize_content(markdown="", plain=document.format == TextDocument.Format.PLAIN)
    return TextVersion.objects.create(
        document=document,
        number=1,
        content=content,
        text=text,
        source=source,
        created_by=user,
        draft_owner=user,
        draft_heartbeat_at=timezone.now(),
    )


@transaction.atomic
def edit(version_id, user, *, source="editor"):
    """Create a draft copied from the current saved version; never resume implicitly."""
    base = get_version(version_id, user)
    document = _document(base.document_id, user, write=True)
    latest = document.versions.filter(state=TextVersion.State.FINAL).order_by("-number").first()
    if base.state != TextVersion.State.FINAL or latest.pk != base.pk:
        raise ConflictError("This version is no longer current. Reload the text.")
    draft = document.versions.filter(state=TextVersion.State.DRAFT).first()
    if draft is not None and not can_recover_orphan(draft, user):
        raise ConflictError("This text already has a draft.")
    if source not in TextVersion.Source.values:
        raise ValidationError("Choose a valid version source.")
    if draft is not None:
        draft.delete()
    return TextVersion.objects.create(
        document=document,
        number=base.number + 1,
        based_on=base,
        content=base.content,
        text=base.text,
        source=source,
        created_by=user,
        draft_owner=user,
        draft_heartbeat_at=timezone.now(),
    )


def _draft(version_id, user, expected_revision, *, recover_orphan=False):
    # Resolve only the parent ID; then serialize all draft operations on that parent.
    document_id = TextVersion.objects.filter(pk=version_id).values_list("document_id", flat=True).first()
    if document_id is None:
        raise ConflictError("This draft no longer exists. Reload the text.")
    document = _document(document_id, user, write=True)
    draft = document.versions.filter(pk=version_id).first()
    if draft is None or draft.state != TextVersion.State.DRAFT:
        raise ConflictError("This version is no longer a draft.")
    if draft.draft_owner_id != user.pk and not (recover_orphan and can_recover_orphan(draft, user)):
        raise PermissionDenied("This draft belongs to another editor.")
    if draft.revision != expected_revision:
        raise ConflictError("This draft changed in another tab. Reload the text.")
    return draft


def _set_content(draft, content, markdown):
    draft.content, draft.text = normalize_content(
        content=content, markdown=markdown, plain=draft.document.format == TextDocument.Format.PLAIN
    )
    draft.revision += 1
    draft.draft_heartbeat_at = timezone.now()


@transaction.atomic
def update_draft(version_id, user, *, expected_revision, content=None, markdown=None):
    draft = _draft(version_id, user, expected_revision)
    _set_content(draft, content, markdown)
    draft.save(update_fields=["content", "text", "revision", "draft_heartbeat_at"])
    return draft


@transaction.atomic
def save(version_id, user, *, expected_revision, content=None, markdown=None):
    draft = _draft(version_id, user, expected_revision)
    _set_content(draft, content, markdown)
    base = draft.based_on
    if base is not None and draft.content == base.content:
        draft.delete()
        return base
    draft.state = TextVersion.State.FINAL
    draft.finalized_at = timezone.now()
    draft.draft_owner = None
    draft.draft_heartbeat_at = None
    draft.save()
    notify_text_version(draft, user)
    return draft


@transaction.atomic
def discard(version_id, user, *, expected_revision):
    draft = _draft(version_id, user, expected_revision, recover_orphan=True)
    draft.delete()


@transaction.atomic
def save_caption(post_id, platform_post_id, user, text, *, base_version_id=None, source="editor"):
    """Save a channel caption as a new version and copy it to the channel variant (F9-B01).

    The same rules as the Caption tab: the post is locked, the channel limit is checked,
    the base must be the newest saved version (``ConflictError`` otherwise) and the caption
    is only ever written by a new version. A channel without history starts it from the
    caption it currently has, so ``base_version_id`` must then be empty.
    Returns ``(version, changed)`` (a ``CaptionSave``); saving an identical text creates no version.

    Every caption write (JR detail, REST, MCP) goes through here, so here a copy carrying an approval
    whose caption changed goes back to review where the approval mode requires it, a planned one out
    of the plan (P-025, P1-B36, P1-B37); ``.returned`` names those copies.
    """
    post = Post.objects.select_for_update().select_related("workspace").get(pk=post_id)
    _authorize(TextDocument(post=post), user, write=True)
    variant = (
        PlatformPost.objects.select_for_update(of=("self",))
        .select_related("social_account")
        .get(pk=platform_post_id, post=post)
    )
    before = reviewed_content(post)
    saved, changed = _save_caption_version(post, variant, user, text, base_version_id, source)
    return CaptionSave(saved, changed, return_edited_to_review(post, before))


def _save_caption_version(post, variant, user, text, base_version_id, source):
    account = variant.social_account
    if account.caption_wire_length(text) > account.char_limit:
        raise ValidationError(f"Caption exceeds the {account.char_limit}-character limit.")
    document = TextDocument.objects.filter(platform_post=variant, kind=TextDocument.Kind.CAPTION).first()
    created = False
    if document is None:
        if base_version_id is not None:
            raise ConflictError("This caption has no saved version yet. Reload the text.")
        if not (variant.effective_caption or "").strip():
            document = create_document(
                post.pk,
                user,
                kind=TextDocument.Kind.CAPTION,
                platform_post_id=variant.pk,
                format=TextDocument.Format.PLAIN,
                content={"text": text},
                source=source,
            )
            saved = document.versions.get(state=TextVersion.State.FINAL)
            variant.platform_specific_caption = saved.text
            variant.save(update_fields=["platform_specific_caption", "updated_at"])
            if text.strip():
                notify_text_version(saved, user)
            return saved, True
        document = create_document(
            post.pk,
            user,
            kind=TextDocument.Kind.CAPTION,
            platform_post_id=variant.pk,
            format=TextDocument.Format.PLAIN,
            content={"text": variant.effective_caption},
            source=source,
        )
        base = document.versions.filter(state=TextVersion.State.FINAL).order_by("-number").first()
        created = True
    else:
        base = (
            document.versions.filter(pk=base_version_id, state=TextVersion.State.FINAL).first()
            if base_version_id is not None
            else None
        )
        if base is None:
            raise ConflictError("This caption changed in another place. Reload the text.")
    draft = edit(base.pk, user, source=source)
    saved = save(draft.pk, user, expected_revision=0, content={"text": text})
    variant.platform_specific_caption = saved.text
    variant.save(update_fields=["platform_specific_caption", "updated_at"])
    return saved, created or saved.pk != base.pk


@transaction.atomic
def copy_concept_to_script(post_id, user, *, source="editor"):
    """Save the newest saved concept as a new script version (P1-B08, "Use as script draft").

    Same rules as any other script write: only text editors, never over somebody's open
    script draft (``ConflictError``), saved versions stay as they are. Copying a text the
    script already has creates no version. Returns ``(version, changed)``.
    """
    post = Post.objects.select_for_update().select_related("workspace").get(pk=post_id)
    _authorize(TextDocument(post=post), user, write=True)
    concept = (
        TextVersion.objects.filter(
            document__post=post, document__kind=TextDocument.Kind.CONCEPT, state=TextVersion.State.FINAL
        )
        .order_by("-number")
        .first()
    )
    if concept is None or not concept.text.strip():
        raise ValidationError("Save a concept version first.")
    script = TextDocument.objects.filter(post=post, kind=TextDocument.Kind.SCRIPT).first()
    if script is None:
        document = create_document(post.pk, user, content=concept.content, source=source)
        return document.versions.get(state=TextVersion.State.FINAL), True
    base = script.versions.filter(state=TextVersion.State.FINAL).order_by("-number").first()
    if script.versions.filter(state=TextVersion.State.DRAFT).exists():
        raise ConflictError("Someone is editing the script. Try again after their draft is saved or discarded.")
    draft = edit(base.pk, user, source=source) if base else start_draft(script.pk, user, source=source)
    saved = save(draft.pk, user, expected_revision=0, content=concept.content)
    return saved, base is None or saved.pk != base.pk


def latest_saved_captions(platform_post_ids):
    """Map PlatformPost id to its newest saved caption version (F6-B02).

    Read-only helper for screens that must show the single source of truth for a
    channel caption. Callers authorise access to the owning post themselves. Channels
    without a saved version are absent from the result.
    """
    versions = TextVersion.objects.filter(
        document__kind=TextDocument.Kind.CAPTION,
        document__platform_post_id__in=list(platform_post_ids),
        state=TextVersion.State.FINAL,
    ).select_related("document")
    # Ordered by number, so the last version written per channel is the newest one.
    return {version.document.platform_post_id: version for version in versions.order_by("number")}
