# JR Content: přidáno 2026-11-14 (blok P2-B15), AGPL-3.0.
"""“Use for another output…”: one uploaded video version for a second output of the same content.

The target output (channel + format) gets a new version that points at the *same* stored file in
FreeFrame (``POST /jrc/versions/{id}/duplicate``): nothing is uploaded or converted again and the
version is ready at once. FreeFrame deletes a stored file only when no version names it any more,
so deleting one branch never takes the other's video. From then on the two outputs are separate:
new versions, Final for publishing and comments of one never touch the other.

"Keep comments?" Yes copies the source version's FreeFrame comments to the new version as they are
(threads, visibility, authors, times, resolved state); No starts the new version without comments.

The right is the same as uploading a version (``uploads.can_upload``), the call is made with the
person's own FreeFrame token, so FreeFrame checks their project role too. The approval rules are
those of every video change (``approvals.edits``): what goes out is the final version, so adding a
version (like uploading one) does not send an approved output back to review; marking it final does.
"""

import logging
import uuid

from django.db import transaction

from apps.composer.models import PlatformPost, Post

from .client import FreeFrameError, FreeFrameUserClient
from .models import VideoAsset, VideoVersion
from .uploads import UploadError, _accounts_ready, _channel_enabled, can_upload

logger = logging.getLogger(__name__)


def reuse_targets(post, source_output):
    """The other outputs of the content a version of ``source_output`` can be used for."""
    from apps.jrc_production.views import enabled_outputs

    return [output for output in enabled_outputs(post) if output.pk != source_output.pk]


def _message(exc):
    code = getattr(exc, "status_code", None)
    if code in (401, 403):
        return "You are not allowed to add video for this client.", 403
    if code == 404:
        return "This video no longer exists in the video service.", 404
    if code == 409:
        return "This version cannot be used right now. Wait until it is converted and try again.", 409
    if code in (400, 422):
        return "The video service refused this request.", 400
    if code == 429:
        return "Too many requests. Try again in a minute.", 429
    return "The video service is unavailable. Try again in a minute.", 503


def _uuid(value):
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        raise UploadError("The video service returned an unexpected answer.", 502) from None


def _count(value, minimum):
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise UploadError("The video service returned an unexpected answer.", 502)
    return value


def reuse_version(user, workspace, post, membership, version_id, *, target, keep_comments):
    """Add the source version's file to the ``target`` output as its next version."""
    from apps.approvals.edits import return_edited_to_review, review_return_notice, reviewed_content
    from apps.jrc_production.notifications import notify_video_version

    if not can_upload(post, membership):
        raise UploadError("You cannot add video for this content.", 403)
    if not isinstance(keep_comments, bool):
        raise UploadError("Choose whether to keep the comments.")
    source = (
        VideoVersion.objects.select_related("video__platform_post__social_account")
        .filter(pk=version_id, video__platform_post__post=post)
        .first()
    )
    if source is None or not _channel_enabled(post, source.video.platform_post):
        raise UploadError("Version not found.", 404)
    if source.status != VideoVersion.Status.READY:
        raise UploadError("Only a converted version can be used for another output.", 409)
    try:
        output = next(o for o in reuse_targets(post, source.video.platform_post) if str(o.pk) == str(target))
    except StopIteration:
        raise UploadError("Choose another output of this content.", 404) from None
    _accounts_ready(user, workspace)
    asset_name = f"{post.title or 'Content'} · {output.output_title}"[:200]

    with transaction.atomic():
        # The content first, then its outputs (reviewed_content locks them): the order of every edit.
        locked_post = Post.objects.select_for_update().select_related("workspace").get(pk=post.pk)
        before = reviewed_content(locked_post)
        locked = PlatformPost.objects.select_for_update(of=("self",)).get(pk=output.pk)
        video = VideoAsset.objects.filter(platform_post=locked).first()
        body = {"copy_comments": keep_comments}
        if video is None:
            body["asset_name"] = asset_name
        else:
            body["asset_id"] = str(video.ff_asset_id)
        try:
            result = FreeFrameUserClient(user).request("POST", f"/jrc/versions/{source.ff_version_id}/duplicate", body)
        except FreeFrameError as exc:
            logger.warning("FreeFrame duplicate of version %s for user %s failed: %s", source.pk, user.pk, exc)
            message, status = _message(exc)
            raise UploadError(message, status) from None
        if not isinstance(result, dict):
            raise UploadError("The video service returned an unexpected answer.", 502)
        ff_asset = _uuid(result.get("asset_id"))
        ff_version = _uuid(result.get("version_id"))
        number = _count(result.get("version_number"), 1)
        copied = _count(result.get("comments_copied"), 0)
        if video is not None and str(video.ff_asset_id) != ff_asset:
            raise UploadError("The video service returned an unexpected answer.", 502)
        try:
            if video is None:
                video = VideoAsset.objects.create(platform_post=locked, ff_asset_id=ff_asset)
            version = VideoVersion.objects.create(
                video=video,
                number=number,
                ff_version_id=ff_version,
                uploaded_by=user,
                status=VideoVersion.Status.READY,
                duration_s=source.duration_s,
                fps=source.fps,
                width=source.width,
                height=source.height,
                original_filename=source.original_filename,
                size_bytes=source.size_bytes,
                copied_from=source,
                copied_from_label=f"v{source.number} · {source.video.platform_post.output_title}"[:300],
                comments_copied=copied if keep_comments else None,
            )
        except Exception:
            # FreeFrame already holds the version; it only names the shared file, so nothing is
            # stored twice. Logged with both ids so it can be matched by hand.
            logger.error("FreeFrame version %s (asset %s) created but not mirrored locally", ff_version, ff_asset)
            raise
        returned = return_edited_to_review(locked_post, before)
        transaction.on_commit(lambda: notify_video_version(version))
    result = {
        "version": str(version.pk),
        "number": version.number,
        "target": str(output.pk),
        "target_label": output.output_title,
        "comments_copied": version.comments_copied,
    }
    notice = review_return_notice(returned, "video")
    if notice:
        result["review_notice"] = notice
    return result
