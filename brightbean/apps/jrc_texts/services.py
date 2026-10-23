"""Version operations. All writers lock the document before accessing its drafts.

ConflictError.status_code is the HTTP status for the F4/F9 adapters. No service
implicitly chooses a draft: mutation always requires its UUID and revision.
"""

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from apps.composer.models import Post
from apps.jrc_production.services import can_create_content, content_membership

from .content import normalize_content
from .models import TextDocument, TextVersion


class ConflictError(Exception):
    status_code = 409


def _authorize(document, user, *, write=False):
    membership = content_membership(document.post.workspace, user)
    if write and not can_create_content(membership):
        raise PermissionDenied("You cannot edit texts for this client.")


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
    if version.state == TextVersion.State.DRAFT and version.draft_owner_id != user.pk:
        raise PermissionDenied("This draft belongs to another editor.")
    return version


def latest_version(document_id, user):
    document = _document(document_id, user)
    return document.versions.filter(state=TextVersion.State.FINAL).order_by("-number").first()


@transaction.atomic
def create_document(post_id, user, *, kind="script", platform_post_id=None, format="rich", content=None, markdown=None):
    post = Post.objects.select_for_update().select_related("workspace").get(pk=post_id)
    # Authorize before validating any channel-specific data.
    probe = TextDocument(post=post)
    _authorize(probe, user, write=True)
    document = TextDocument(
        post=post, workspace=post.workspace, kind=kind, platform_post_id=platform_post_id, format=format
    )
    document.full_clean()
    normalized, text = normalize_content(content=content, markdown=markdown, plain=format == "plain")
    document.save()
    TextVersion.objects.create(
        document=document,
        number=1,
        state=TextVersion.State.FINAL,
        content=normalized,
        text=text,
        created_by=user,
        finalized_at=timezone.now(),
    )
    return document


@transaction.atomic
def edit(version_id, user, *, source="editor"):
    """Create a draft copied from the current saved version; never resume implicitly."""
    base = get_version(version_id, user)
    document = _document(base.document_id, user, write=True)
    latest = document.versions.filter(state=TextVersion.State.FINAL).order_by("-number").first()
    if base.state != TextVersion.State.FINAL or latest.pk != base.pk:
        raise ConflictError("This version is no longer current. Reload the text.")
    if document.versions.filter(state=TextVersion.State.DRAFT).exists():
        raise ConflictError("This text already has a draft.")
    if source not in TextVersion.Source.values:
        raise ValidationError("Choose a valid version source.")
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


def _draft(version_id, user, expected_revision):
    # Resolve only the parent ID; then serialize all draft operations on that parent.
    document_id = TextVersion.objects.filter(pk=version_id).values_list("document_id", flat=True).first()
    if document_id is None:
        raise ConflictError("This draft no longer exists. Reload the text.")
    document = _document(document_id, user, write=True)
    draft = document.versions.filter(pk=version_id).first()
    if draft is None or draft.state != TextVersion.State.DRAFT:
        raise ConflictError("This version is no longer a draft.")
    if draft.draft_owner_id != user.pk:
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
    return draft


@transaction.atomic
def discard(version_id, user, *, expected_revision):
    draft = _draft(version_id, user, expected_revision)
    draft.delete()
