"""Shared eligibility for downloading a channel's final video and thumbnail."""

from apps.composer.models import PlatformPost
from apps.jrc_texts.models import TextDocument
from apps.members.permissions import can_publish_directly, release_blocker

from .models import VideoAsset, VideoVersion


def _final_ready(video):
    return bool(video and video.final_version_id and video.final_version.status == VideoVersion.Status.READY)


def missing_for_publication(variant, *, caption, video, download=False):
    """What this channel copy still needs before it may be marked as published or its final files downloaded.

    One list for the server rule and for the texts of disabled buttons (P1-B27): the approval
    workflow of the workspace (``release_blocker``: a draft goes out directly only where the mode
    does not require approval), a saved caption and, when there is a video (always for a download),
    a ready version marked final. Publishing rights are checked separately.
    """
    missing = []
    if variant.status != PlatformPost.Status.PUBLISHED:
        reason = release_blocker(variant.post.workspace, variant.status)
        if reason:
            missing.append(reason)
    if not caption.strip():
        missing.append("Save a caption.")
    if (video or download) and not _final_ready(video):
        missing.append("Mark a ready video as “Final for publishing”.")
    return missing


def can_download_final(membership, variant, *, caption, video):
    """Use the same rule in download endpoints and when displaying their links."""
    return bool(
        can_publish_directly(membership)
        and not variant.post.workspace.is_archived
        and variant.status
        in {
            PlatformPost.Status.DRAFT,
            PlatformPost.Status.APPROVED,
            PlatformPost.Status.SCHEDULED,
            PlatformPost.Status.PUBLISHED,
        }
        and not missing_for_publication(variant, caption=caption, video=video, download=True)
    )


def channel_caption(variant):
    """The latest saved caption of a channel copy, falling back to its effective caption."""
    document = TextDocument.objects.filter(platform_post=variant, kind=TextDocument.Kind.CAPTION).first()
    latest_caption = document.versions.filter(state="final").order_by("-number").first() if document else None
    return latest_caption.text if latest_caption else (variant.effective_caption or "")


def downloadable_final(membership, variant):
    """Return the ready final only when the channel meets all download conditions."""
    video = VideoAsset.objects.filter(platform_post=variant).select_related("final_version").first()
    return video if can_download_final(membership, variant, caption=channel_caption(variant), video=video) else None
