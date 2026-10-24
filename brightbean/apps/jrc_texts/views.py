"""Local Script tab adapter; document services remain the authority for writes."""

import json
from uuid import UUID

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from apps.composer.models import Post
from apps.jrc_production.services import content_membership

from . import services
from .content import MAX_CONTENT_BYTES
from .models import TextDocument, TextVersion

MAX_COMPARE_BYTES = 250_000


def _version(version, *, content=False):
    data = {
        "id": str(version.pk),
        "number": version.number,
        "revision": version.revision,
        "created_at": version.created_at.isoformat(),
        "saved_at": version.finalized_at.isoformat() if version.finalized_at else None,
        "author": version.created_by.display_name if version.created_by else "Former member",
    }
    if content:
        data["content"] = version.content
    return data


def _snapshot(post, user, membership, selected=None, compare_old=None, compare_new=None):
    if bool(compare_old) != bool(compare_new):
        raise ValidationError("Choose both versions to compare.")
    can_edit = services.can_edit_text(post, membership)
    result = {"can_edit": can_edit, "versions": [], "latest": None, "shown": None, "draft": None}
    document = TextDocument.objects.filter(post=post, kind="script").first()
    if document is None:
        if selected or compare_old or compare_new:
            get_object_or_404(
                TextVersion,
                pk=selected or compare_old or compare_new,
                document__post=post,
                document__kind="script",
                state="final",
            )
        return result
    versions = list(document.versions.filter(state="final").select_related("created_by").order_by("-number"))
    if not versions and (selected or compare_old or compare_new):
        get_object_or_404(document.versions.filter(state="final"), pk=selected or compare_old or compare_new)
    result["versions"] = [_version(version) for version in versions]
    if versions:
        result["latest"] = str(versions[0].pk)
        shown = get_object_or_404(document.versions.filter(state="final"), pk=selected) if selected else versions[0]
        result["shown"] = _version(shown, content=True)
        if compare_old or compare_new:
            old = get_object_or_404(
                document.versions.filter(state="final").select_related("created_by"), pk=compare_old
            )
            new = get_object_or_404(
                document.versions.filter(state="final").select_related("created_by"), pk=compare_new
            )
        elif len(versions) >= 2:
            old, new = versions[1], versions[0]
        else:
            old = new = None
        if old and new:
            size = len(json.dumps(old.content, ensure_ascii=False).encode()) + len(
                json.dumps(new.content, ensure_ascii=False).encode()
            )
            if size > MAX_COMPARE_BYTES:
                result["comparison_error"] = "These versions are too large to compare."
            result["comparison"] = {
                "old": _version(old, content=size <= MAX_COMPARE_BYTES),
                "new": _version(new, content=size <= MAX_COMPARE_BYTES),
            }
    draft = document.versions.filter(state="draft").select_related("created_by", "draft_owner").first()
    if draft:
        own = draft.draft_owner_id == user.pk and can_edit
        result["draft"] = {
            "id": str(draft.pk),
            "number": draft.number,
            "revision": draft.revision,
            "owner": draft.draft_owner.display_name if draft.draft_owner else "Former member",
            "since": draft.created_at.isoformat(),
            "own": own,
            "recoverable": can_edit and services.can_recover_orphan(draft, user),
        }
        if own:
            result["draft"]["content"] = draft.content
    return result


def _uuid(value):
    try:
        return UUID(value)
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValidationError("Choose a valid text version.") from exc


@login_required
@never_cache
@require_http_methods(["GET", "POST"])
def script(request, workspace_id, post_id):
    post = get_object_or_404(Post.objects.select_related("workspace"), pk=post_id, workspace_id=workspace_id)
    membership = content_membership(post.workspace, request.user)
    try:
        if request.method == "GET":
            selected = _uuid(request.GET["version"]) if request.GET.get("version") else None
            compare_old = _uuid(request.GET["compare_old"]) if request.GET.get("compare_old") else None
            compare_new = _uuid(request.GET["compare_new"]) if request.GET.get("compare_new") else None
            return JsonResponse(_snapshot(post, request.user, membership, selected, compare_old, compare_new))
        if not services.can_edit_text(post, membership):
            raise PermissionDenied("You cannot edit texts for this post.")
        if len(request.body) > MAX_CONTENT_BYTES + 4096:
            return JsonResponse({"error": "Text is too large."}, status=400)
        try:
            data = json.loads(request.body)
        except (ValueError, UnicodeDecodeError, RecursionError) as exc:
            raise ValidationError("Send a valid JSON object.") from exc
        if not isinstance(data, dict):
            raise ValidationError("Send a valid JSON object.")
        action = data.get("action")
        if not isinstance(action, str):
            raise ValidationError("Choose a valid script action.")
        if action == "edit":
            # Serialize first creation as well as two simultaneous Edit clicks.
            with transaction.atomic():
                Post.objects.select_for_update().get(pk=post.pk)
                document = TextDocument.objects.filter(post=post, kind="script").first()
                if document is None:
                    if data.get("base") is not None:
                        raise services.ConflictError("The script changed. Reload the text.")
                    document = services.create_document(post.pk, request.user, markdown="")
                    base = services.latest_version(document.pk, request.user)
                else:
                    if data.get("base") is None:
                        raise services.ConflictError("The script changed. Reload the text.")
                    base = get_object_or_404(document.versions.filter(state="final"), pk=_uuid(data.get("base")))
                services.edit(base.pk, request.user)
                return JsonResponse(_snapshot(post, request.user, membership))
        if action not in {"autosave", "save", "discard"}:
            raise ValidationError("Choose a valid script action.")
        version_id = _uuid(data.get("version"))
        revision = data.get("revision")
        if type(revision) is not int or revision < 0:
            raise ValidationError("Provide the draft revision.")
        # Never resolve a caller-supplied draft outside this post or text kind.
        if not TextVersion.objects.filter(pk=version_id, document__post=post, document__kind="script").exists():
            raise services.ConflictError("This draft no longer exists. Reload the text.")
        if action == "discard":
            services.discard(version_id, request.user, expected_revision=revision)
        else:
            if not isinstance(data.get("content"), dict):
                raise ValidationError("Provide the editor content.")
            operation = services.update_draft if action == "autosave" else services.save
            operation(version_id, request.user, expected_revision=revision, content=data["content"])
        return JsonResponse(_snapshot(post, request.user, membership))
    except services.ConflictError as exc:
        return JsonResponse({"error": str(exc)}, status=409)
    except ValidationError as exc:
        return JsonResponse({"error": " ".join(exc.messages)}, status=400)
