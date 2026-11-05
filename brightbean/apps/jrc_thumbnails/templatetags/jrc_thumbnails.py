# JR Content: přidáno 2026-11-04 (blok P1-B10), AGPL-3.0.
from django import template
from django.urls import reverse

from .. import services
from ..models import ChannelThumbnail

register = template.Library()


def _membership(context):
    """One lookup per page even when several channels render a thumbnail."""
    cache = context.render_context
    if "jrc_thumbnail_membership" not in cache:
        request = context.get("request")
        workspace = context.get("workspace")
        cache["jrc_thumbnail_membership"] = (
            services.member(workspace, request.user) if request is not None and workspace is not None else None
        )
    return cache["jrc_thumbnail_membership"]


@register.inclusion_tag("jrc_thumbnails/_channel.html", takes_context=True)
def channel_thumbnail(context, variant, mode="caption", label=""):
    """mode: "caption" and "publish" may upload, "approval" only shows the current image."""
    if variant is None:
        return {"show": False}
    post = context.get("post")
    membership = _membership(context)
    versions = list(
        ChannelThumbnail.objects.filter(platform_post=variant).select_related("uploaded_by").order_by("-number")
    )
    current = versions[0] if versions else None
    may_download = mode != "approval" and current is not None and services.can_download(membership, variant)
    kwargs = {"workspace_id": variant.post.workspace_id, "post_id": variant.post_id, "platform_post_id": variant.pk}

    def download_url(thumbnail):
        if not may_download:
            return ""
        return reverse("jrc_thumbnails:download", kwargs={**kwargs, "thumbnail_id": thumbnail.pk})

    return {
        "show": True,
        "mode": mode,
        "label": label or f"{variant.social_account.get_platform_display()} · {variant.social_account.account_name}",
        "variant": variant,
        "current": current,
        "current_url": current.image.url if current else "",
        "download_url": download_url(current) if current else "",
        "history": [
            {"thumbnail": item, "url": item.image.url, "download_url": download_url(item)} for item in versions[1:]
        ],
        "can_upload": mode != "approval" and post is not None and services.can_upload(post, membership),
        "upload_url": reverse("jrc_thumbnails:upload", kwargs=kwargs),
        "max_bytes": services.MAX_BYTES,
        "csrf_token": context.get("csrf_token"),
    }
