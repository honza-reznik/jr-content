"""Reversible content deletion. Row locks follow the publisher: content, then copies."""

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from apps.calendar.models import QueueEntry
from apps.composer.models import PlatformPost, Post
from apps.members.permissions import content_delete_blocker

from .models import VideoDeletionRequest
from .services import content_membership


def delete_remote_video(asset_id, *, api=None):
    from apps.jrc_video.client import FreeFrameClient, FreeFrameError

    try:
        (api or FreeFrameClient()).request("DELETE", f"/assets/{asset_id}")
    except FreeFrameError as exc:
        if exc.status_code != 404:
            VideoDeletionRequest.objects.filter(pk=asset_id).update(
                last_error="FreeFrame cleanup unavailable; retry pending."
            )
            return False
    VideoDeletionRequest.objects.filter(pk=asset_id).delete()
    return True


@transaction.atomic
def trash_content(post, user):
    post = Post.objects.select_for_update(of=("self",)).select_related("workspace").get(pk=post.pk)
    membership = content_membership(post.workspace, user)
    copies = list(PlatformPost.all_objects.select_for_update().filter(post=post).order_by("pk"))
    blocker = content_delete_blocker(membership, post, copies)
    if blocker:
        raise PermissionDenied(blocker)
    if any(copy.status == "publishing" for copy in copies):
        raise ValidationError("This content is publishing. Wait until publishing finishes before moving it to Trash.")
    for copy in copies:
        # A restore never requeues a cancelled publication or a failed retry.
        if copy.status in {"scheduled", "failed"}:
            copy.transition_to("draft")
        copy.scheduled_at = None
        copy.next_retry_at = None
        copy.save(update_fields=[*PlatformPost.TRANSITION_FIELDS, "scheduled_at", "updated_at"])
    QueueEntry.objects.filter(post=post).delete()
    post.trashed_at = timezone.now()
    post.scheduled_at = None
    post.save(update_fields=["trashed_at", "scheduled_at", "updated_at"])
    return post


@transaction.atomic
def manage_trash(post_id, user, *, permanently=False):
    post = (
        Post.all_objects.select_for_update(of=("self",))
        .select_related("workspace")
        .get(pk=post_id, trashed_at__isnull=False)
    )
    membership = content_membership(post.workspace, user)
    copies = list(PlatformPost.all_objects.select_for_update().filter(post=post).order_by("pk"))
    blocker = content_delete_blocker(membership, post, copies)
    if blocker:
        raise PermissionDenied(blocker)
    if permanently:
        from apps.jrc_video.models import VideoAsset

        asset_ids = list(VideoAsset.objects.filter(platform_post__post=post).values_list("ff_asset_id", flat=True))
        for asset_id in asset_ids:
            VideoDeletionRequest.objects.get_or_create(ff_asset_id=asset_id)
            transaction.on_commit(lambda asset_id=asset_id: delete_remote_video(asset_id))
        post.delete()
    else:
        post.trashed_at = None
        post.save(update_fields=["trashed_at", "updated_at"])
    return post
