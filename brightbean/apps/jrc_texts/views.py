"""Local Script and Concept tab adapter; document services remain the authority for writes.

P1-B08: the Concept tab uses the same editor endpoint with ``kind="concept"``.
"""

import json
from uuid import UUID

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods, require_POST

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
        "name": version.name,
        "revision": version.revision,
        "created_at": version.created_at.isoformat(),
        "saved_at": version.finalized_at.isoformat() if version.finalized_at else None,
        "author": version.created_by.display_name if version.created_by else "Former member",
    }
    if content:
        data["content"] = version.content
    return data


def _snapshot(post, user, membership, selected=None, compare_old=None, compare_new=None, *, kind="script"):
    if bool(compare_old) != bool(compare_new):
        raise ValidationError("Choose both versions to compare.")
    can_edit = services.can_edit_text(post, membership)
    result = {"can_edit": can_edit, "versions": [], "latest": None, "shown": None}
    document = TextDocument.objects.filter(post=post, kind=kind).first()
    if document is None:
        if selected or compare_old or compare_new:
            get_object_or_404(
                TextVersion,
                pk=selected or compare_old or compare_new,
                document__post=post,
                document__kind=kind,
                state="final",
            )
        return result
    versions = list(document.versions.filter(state="final").select_related("created_by", "label").order_by("-number"))
    if not versions and (selected or compare_old or compare_new):
        get_object_or_404(document.versions.filter(state="final"), pk=selected or compare_old or compare_new)
    result["versions"] = [_version(version) for version in versions]
    if versions:
        result["latest"] = str(versions[0].pk)
        shown = get_object_or_404(document.versions.filter(state="final"), pk=selected) if selected else versions[0]
        result["shown"] = _version(shown, content=True)
        if compare_old or compare_new:
            old = get_object_or_404(
                document.versions.filter(state="final").select_related("created_by", "label"), pk=compare_old
            )
            new = get_object_or_404(
                document.versions.filter(state="final").select_related("created_by", "label"), pk=compare_new
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
    return result


def _saved(result):
    """What the editor needs after an automatic save (P2-B08)."""
    version, concurrent = result.version, result.concurrent
    return {
        "version": _version(version) if version else None,
        "created": result.created,
        "changed": result.changed,
        "concurrent": {"number": concurrent.number, "author": _version(concurrent)["author"]} if concurrent else None,
    }


def _uuid(value):
    try:
        return UUID(value)
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValidationError("Choose a valid text version.") from exc


def _text_editor(request, workspace_id, post_id, kind):
    post = get_object_or_404(Post.objects.select_related("workspace"), pk=post_id, workspace_id=workspace_id)
    membership = content_membership(post.workspace, request.user)
    try:
        if request.method == "GET":
            selected = _uuid(request.GET["version"]) if request.GET.get("version") else None
            compare_old = _uuid(request.GET["compare_old"]) if request.GET.get("compare_old") else None
            compare_new = _uuid(request.GET["compare_new"]) if request.GET.get("compare_new") else None
            return JsonResponse(
                _snapshot(post, request.user, membership, selected, compare_old, compare_new, kind=kind)
            )
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
        # P2-B08: the editor saves automatically (no Edit, Save or drafts); the service applies the
        # version rule (same person, same device, 15 minutes) and never writes over another version.
        if data.get("action") != "autosave":
            raise ValidationError(f"Choose a valid {kind} action.")
        base = _uuid(data["base"]) if data.get("base") is not None else None
        revision = data.get("revision")
        if base is not None and (type(revision) is not int or revision < 0):
            raise ValidationError("Provide the version revision.")
        if not isinstance(data.get("content"), dict):
            raise ValidationError("Provide the editor content.")
        # Never resolve a caller-supplied version outside this post or text kind.
        if (
            base is not None
            and not TextVersion.objects.filter(
                pk=base, document__post=post, document__kind=kind, state="final"
            ).exists()
        ):
            raise services.ConflictError(f"This {kind} changed in another place. Reload the text.")
        result = services.autosave_text(
            post.pk,
            request.user,
            kind=kind,
            content=data["content"],
            base_version_id=base,
            revision=revision,
            device=data.get("device"),
        )
        snapshot = _snapshot(post, request.user, membership, kind=kind)
        snapshot["saved"] = _saved(result)
        return JsonResponse(snapshot)
    except services.ConflictError as exc:
        return JsonResponse({"error": str(exc)}, status=409)
    except ValidationError as exc:
        return JsonResponse({"error": " ".join(exc.messages)}, status=400)


@login_required
@never_cache
@require_http_methods(["GET", "POST"])
def script(request, workspace_id, post_id):
    return _text_editor(request, workspace_id, post_id, "script")


@login_required
@never_cache
@require_http_methods(["GET", "POST"])
def concept(request, workspace_id, post_id):
    """P1-B08: the idea worked out in versions; the same rules as the script."""
    return _text_editor(request, workspace_id, post_id, "concept")


@login_required
@require_POST
def concept_to_script(request, workspace_id, post_id):
    """P1-B08 "Use as script draft": the newest saved concept becomes a new script version."""
    post = get_object_or_404(Post.objects.select_related("workspace"), pk=post_id, workspace_id=workspace_id)
    membership = content_membership(post.workspace, request.user)
    if not services.can_edit_text(post, membership):
        raise PermissionDenied("You cannot edit texts for this post.")
    detail = reverse("jrc_production:content_detail", kwargs={"workspace_id": post.workspace_id, "post_id": post.pk})
    try:
        version, changed = services.copy_concept_to_script(post.pk, request.user)
    except services.ConflictError as exc:
        messages.error(request, str(exc))
        return redirect(f"{detail}?tab=concept")
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
        return redirect(f"{detail}?tab=concept")
    if changed:
        messages.success(request, f"The concept was copied to the script as version {version.number}.")
    else:
        messages.info(request, "The script already matches the latest concept.")
    return redirect(f"{detail}?tab=script")


@login_required
@require_POST
def version_name(request, workspace_id, post_id, version_id):
    post = get_object_or_404(Post, pk=post_id, workspace_id=workspace_id)
    content_membership(post.workspace, request.user)
    version = get_object_or_404(TextVersion, pk=version_id, document__post=post, state="final")
    try:
        name = services.name_version(version.pk, request.user, request.POST.get("name", ""))
    except ValidationError as exc:
        return JsonResponse({"error": " ".join(exc.messages)}, status=400)
    return JsonResponse({"name": name, "number": version.number})
