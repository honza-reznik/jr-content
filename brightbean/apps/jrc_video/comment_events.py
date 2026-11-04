"""Authenticated local FreeFrame comment events; never accept comment text."""

import hashlib
import hmac
import json
import time
import uuid

from django.conf import settings
from django.db import transaction
from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from apps.jrc_production.notifications import notify_client_comment
from apps.members.models import WorkspaceMembership

from .models import VideoAccount, VideoAsset, VideoCommentReceipt


@csrf_exempt
@require_POST
def comment_event(request):
    secret = getattr(settings, "JRC_FF_SSO_SECRET", "")
    if len(request.body) > 2048 or len(secret) < 32:
        return HttpResponse(status=403)
    expected = hmac.new(secret.encode(), b"jrc-video-comment.v1." + request.body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected.encode(), request.headers.get("X-JRC-Signature", "").encode()):
        return HttpResponse(status=403)
    try:
        data = json.loads(request.body)
        comment_id = uuid.UUID(data["comment_id"])
        asset_id = uuid.UUID(data["asset_id"])
        author_id = uuid.UUID(data["author_id"])
        if abs(time.time() - int(data["timestamp"])) > 60:
            return HttpResponse(status=403)
    except (ValueError, TypeError, KeyError):
        return HttpResponse(status=400)
    video = VideoAsset.objects.select_related("platform_post__post__workspace").filter(ff_asset_id=asset_id).first()
    account = VideoAccount.objects.select_related("user").filter(ff_user_id=author_id).first()
    if video is None or account is None:
        return HttpResponse(status=404)
    post = video.platform_post.post
    membership = WorkspaceMembership.objects.filter(workspace=post.workspace, user=account.user).first()
    if (
        membership is None
        or membership.workspace_role != "client"
        or not account.user.is_active
        or post.workspace.is_archived
    ):
        return HttpResponse(status=204)
    with transaction.atomic():
        _, created = VideoCommentReceipt.objects.get_or_create(comment_id=comment_id)
        if created:
            notify_client_comment(post, account.user, tab="video", isolate=False)
    return HttpResponse(status=204)
