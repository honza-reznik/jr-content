"""JSON endpoints behind the "Upload version" button (F5-B05). Session login + CSRF on every call."""

import json
from urllib.parse import urlencode

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_GET, require_POST

from apps.composer.models import Post
from apps.members.models import WorkspaceMembership
from apps.members.permissions import can_publish_directly
from apps.workspaces.models import Workspace

from . import player, uploads
from .downloads import can_download_version, downloadable_final
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


@_secure
@require_POST
@_json
def final(request, workspace_id, post_id, version_id):
    _workspace, membership, post = _context(request, workspace_id, post_id)
    data = _body(request)
    return player.set_final(request.user, post, membership, version_id, final=data.get("final", True) is not False)


@_secure
@require_GET
def final_download(request, workspace_id, post_id, platform_post_id):
    """Download this channel's final through the session-authorized file gateway."""
    from apps.composer.models import PlatformPost

    _workspace, membership, post = _context(request, workspace_id, post_id)
    if not can_publish_directly(membership):
        raise PermissionDenied("You cannot download the final video.")
    variant = get_object_or_404(PlatformPost.objects.select_related("social_account"), pk=platform_post_id, post=post)
    video = downloadable_final(membership, variant)
    if video is None:
        raise Http404
    return _video_download(request, video.ff_asset_id, video.final_version.ff_version_id)


@_secure
@require_GET
def version_download(request, workspace_id, post_id, version_id):
    from .models import VideoVersion

    _workspace, membership, post = _context(request, workspace_id, post_id)
    version = get_object_or_404(
        VideoVersion.objects.select_related("video__platform_post__post__workspace"),
        pk=version_id,
        video__platform_post__post=post,
        status=VideoVersion.Status.READY,
    )
    # FreeFrame lets every project member (client and viewer included) download, so the rule is
    # enforced here, the same one that decides whether the Download version link is shown.
    if not can_download_version(membership, version.video.platform_post, version):
        raise PermissionDenied("You cannot download this video version.")
    return _video_download(request, version.video.ff_asset_id, version.ff_version_id)


def _video_download(request, asset_id, version_id):
    from apps.jrc_storage.views import proxy_video_file

    from .client import FreeFrameClient, FreeFrameError
    from .models import VideoAccount

    api = FreeFrameClient()
    path = f"/jrc-sync/assets/{asset_id}"
    try:
        result = api.request("GET", f"{path}/download-info?{urlencode({'version_id': version_id})}")
        if not isinstance(result, dict) or not isinstance(result.get("filename"), str):
            raise FreeFrameError("Invalid download response")
    except FreeFrameError:
        return HttpResponse("Video download is unavailable.", status=503, content_type="text/plain")
    # The confirmation link returns here: access is checked again, never a generic raw-file URL.
    if result.get("download_note") and request.GET.get("converted") != "1":
        response = render(request, "jrc_video/download_fallback.html", {"download_url": request.path + "?converted=1"})
    else:
        account = VideoAccount.objects.filter(user=request.user).first()
        if account is None:
            raise Http404
        response = proxy_video_file(
            request,
            api,
            f"{path}/download",
            params={"version_id": str(version_id), "actor_id": str(account.ff_user_id)},
        )
    response["Cache-Control"] = "no-store"
    response["Referrer-Policy"] = "no-referrer"
    return response
