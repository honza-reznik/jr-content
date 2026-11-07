"""Video tab: the embedded player and "Final for publishing" (F5-B08).

The player itself is FreeFrame's embedded viewer (``?embed=1``); this module only decides
who may mark a version as the one to publish and remembers the choice in Brightbean.
"""

from django.db import transaction
from django.utils import timezone

from .models import VideoActivity, VideoAsset, VideoVersion
from .uploads import UploadError


def can_mark_final(post, membership):
    """The team members who may approve content; clients, viewers and non-members never mark."""
    from apps.jrc_production.services import is_client

    return bool(
        membership is not None
        and not post.workspace.is_archived
        and not is_client(membership)
        and membership.effective_permissions.get("approve_posts", False)
    )


def set_final(user, post, membership, version_id, *, final=True):
    """Mark (or unmark) one converted version of a channel video as final for publishing.

    The final version is what goes out, so a change of it after approval sends the copy back to
    review where the mode requires approval, a planned one out of the plan (P-025, P1-B37);
    ``review_notice`` in the result then says so.
    """
    from apps.approvals.edits import return_edited_to_review, review_return_notice, reviewed_content
    from apps.composer.models import Post

    if not can_mark_final(post, membership):
        raise UploadError("You cannot mark a final version for this content.", 403)
    with transaction.atomic():
        # Same lock order as caption saves and thumbnail uploads: the content first.
        post = Post.objects.select_for_update().select_related("workspace").get(pk=post.pk)
        before = reviewed_content(post)
        version = (
            VideoVersion.objects.filter(pk=version_id, video__platform_post__post=post).select_related("video").first()
        )
        if version is None:
            raise UploadError("Version not found.", 404)
        # One decision at a time per channel video: the latest click wins, never two finals.
        video = VideoAsset.objects.select_for_update().get(pk=version.video_id)
        if final:
            if video.final_version_id == version.pk:
                return {"final": str(version.pk)}
            version.refresh_from_db()
            if version.status != VideoVersion.Status.READY:
                raise UploadError("Only a converted version can be marked as final.", 409)
            video.final_version = version
            video.final_by = user
            video.final_at = timezone.now()
        elif video.final_version_id == version.pk:
            video.final_version = None
            video.final_by = None
            video.final_at = None
        else:
            return {"final": None}
        VideoActivity.objects.create(
            version=version,
            actor=user,
            action=VideoActivity.Action.FINAL if final else VideoActivity.Action.UNFINAL,
        )
        video.save(update_fields=["final_version", "final_by", "final_at"])
        returned = return_edited_to_review(post, before)
    result = {"final": str(version.pk) if final else None}
    notice = review_return_notice(returned, "final video")
    if notice:
        result["review_notice"] = notice
    return result
