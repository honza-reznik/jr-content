"""JSON endpoints behind the "Upload version" button (F5-B05). Session login + CSRF on every call."""

import json

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_GET, require_POST

from apps.composer.models import Post
from apps.members.models import WorkspaceMembership
from apps.workspaces.models import Workspace

from . import uploads
from .uploads import UploadError

MAX_BODY_BYTES = 8192


def _context(request, workspace_id, post_id):
    workspace = get_object_or_404(Workspace, pk=workspace_id)
    membership = WorkspaceMembership.objects.filter(user=request.user, workspace=workspace).first()
    if membership is None:
        raise PermissionDenied("You are not a member of this workspace.")
    post = get_object_or_404(Post.objects.select_related("workspace"), pk=post_id, workspace=workspace)
    return workspace, membership, post


def _body(request):
    if len(request.body) > MAX_BODY_BYTES:
        raise UploadError("Request too large.", 413)
    try:
        data = json.loads(request.body or b"{}")
    except ValueError:
        raise UploadError("Invalid request.") from None
    if not isinstance(data, dict):
        raise UploadError("Invalid request.")
    return data


def _json(handler):
    """Run ``handler`` and turn UploadError into a small JSON answer without internal details."""

    def run(request, *args, **kwargs):
        try:
            return JsonResponse(handler(request, *args, **kwargs))
        except UploadError as exc:
            return JsonResponse({"error": str(exc)}, status=exc.status)

    run.__name__ = handler.__name__
    return run


def _secure(view):
    return never_cache(login_required(csrf_protect(view)))


@_secure
@require_POST
@_json
def start(request, workspace_id, post_id):
    workspace, membership, post = _context(request, workspace_id, post_id)
    data = _body(request)
    return uploads.start_upload(
        request.user,
        workspace,
        post,
        membership,
        channel=data.get("channel"),
        filename=data.get("filename"),
        size=data.get("size"),
        browser_type=data.get("type") or "",
    )


@_secure
@require_GET
@_json
def resume(request, workspace_id, post_id, version_id):
    workspace, _membership, post = _context(request, workspace_id, post_id)
    return uploads.resume_info(request.user, workspace, post, version_id)


@_secure
@require_POST
@_json
def part(request, workspace_id, post_id, version_id):
    workspace, _membership, post = _context(request, workspace_id, post_id)
    data = _body(request)
    url = uploads.part_url(
        request.user,
        workspace,
        post,
        version_id,
        part=data.get("part"),
        s3_key=data.get("s3_key"),
        upload_id=data.get("upload_id"),
    )
    return {"url": url}


@_secure
@require_POST
@_json
def complete(request, workspace_id, post_id, version_id):
    workspace, _membership, post = _context(request, workspace_id, post_id)
    data = _body(request)
    return uploads.complete_upload(
        request.user, workspace, post, version_id, s3_key=data.get("s3_key"), upload_id=data.get("upload_id")
    )


@_secure
@require_POST
@_json
def abort(request, workspace_id, post_id, version_id):
    workspace, _membership, post = _context(request, workspace_id, post_id)
    data = _body(request)
    return uploads.abort_upload(
        request.user, workspace, post, version_id, s3_key=data.get("s3_key"), upload_id=data.get("upload_id")
    )


@_secure
@require_GET
@_json
def status(request, workspace_id, post_id):
    _workspace, membership, post = _context(request, workspace_id, post_id)
    fresh = uploads.refresh_statuses(request.user, post)
    return {"fresh": fresh, "channels": uploads.channel_cards(request.user, post, membership)}
