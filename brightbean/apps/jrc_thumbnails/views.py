# JR Content: přidáno 2026-11-04 (blok P1-B10), AGPL-3.0.
"""Upload and download of channel thumbnails; bytes are read only via the P1-B03 file gateway."""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from apps.approvals.edits import review_return_notice
from apps.composer.models import PlatformPost, Post
from apps.members.permissions import can_publish_directly
from apps.workspaces.models import Workspace

from . import services
from .models import ChannelThumbnail

RETURN_TABS = {"caption", "publish"}


def _context(request, workspace_id, post_id, platform_post_id):
    workspace = get_object_or_404(Workspace, pk=workspace_id)
    membership = services.member(workspace, request.user)
    if membership is None:
        raise Http404
    post = get_object_or_404(Post, pk=post_id, workspace=workspace)
    variant = get_object_or_404(PlatformPost.objects.select_related("social_account"), pk=platform_post_id, post=post)
    return workspace, membership, post, variant


@login_required
@require_POST
def upload(request, workspace_id, post_id, platform_post_id):
    workspace, membership, post, variant = _context(request, workspace_id, post_id, platform_post_id)
    if not services.can_upload(post, membership):
        raise PermissionDenied("You cannot upload thumbnails for this content.")
    try:
        thumbnail = services.upload_thumbnail(post, variant, request.user, request.FILES.get("image"), membership)
    except ValidationError as exc:
        request.session[f"thumbnail_error:{variant.pk}"] = " ".join(exc.messages)
    else:
        notice = review_return_notice(thumbnail.returned, "thumbnail")
        messages.success(request, f"Thumbnail uploaded as version {thumbnail.number}. {notice}".strip())
    tab = request.POST.get("tab")
    tab = tab if tab in RETURN_TABS else "caption"
    detail = reverse("jrc_production:content_detail", kwargs={"workspace_id": workspace.id, "post_id": post.id})
    return redirect(f"{detail}?tab={tab}#thumbnail-{variant.id}")


@login_required
@require_GET
def download(request, workspace_id, post_id, platform_post_id, thumbnail_id):
    _workspace, membership, post, variant = _context(request, workspace_id, post_id, platform_post_id)
    if not can_publish_directly(membership):
        raise PermissionDenied("You cannot download this thumbnail.")
    if not services.can_download(membership, variant):
        raise Http404
    thumbnail = get_object_or_404(
        ChannelThumbnail.objects.select_related("post", "platform_post__social_account"),
        pk=thumbnail_id,
        platform_post=variant,
        post=post,
    )
    from apps.jrc_storage.views import stored_file

    response = stored_file(request, thumbnail.image.name, workspace=post.workspace)
    if response.status_code in (200, 206):
        response["Content-Disposition"] = f'attachment; filename="{services.download_filename(thumbnail)}"'
    return response
