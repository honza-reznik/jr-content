"""Shared eligibility for downloading a channel's final video and thumbnail."""

from apps.composer.models import PlatformPost
from apps.jrc_texts.models import TextDocument
from apps.members.permissions import can_publish_directly

from .models import VideoAsset, VideoVersion


def can_download_final(membership, variant, *, caption, video):
    """Use the same rule in download endpoints and when displaying their links."""
    return bool(
        can_publish_directly(membership)
        and not variant.post.workspace.is_archived
        and variant.status in {PlatformPost.Status.APPROVED, PlatformPost.Status.SCHEDULED}
        and caption.strip()
        and video
        and video.final_version_id
        and video.final_version.status == VideoVersion.Status.READY
    )


def downloadable_final(membership, variant):
    """Return the ready final only when the channel meets all download conditions."""
    document = TextDocument.objects.filter(platform_post=variant, kind=TextDocument.Kind.CAPTION).first()
    latest_caption = document.versions.filter(state="final").order_by("-number").first() if document else None
    caption = latest_caption.text if latest_caption else (variant.effective_caption or "")
    video = VideoAsset.objects.filter(platform_post=variant).select_related("final_version").first()
    return video if can_download_final(membership, variant, caption=caption, video=video) else None
