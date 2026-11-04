# JR Content: přidáno 2026-11-04 (blok P1-B10), AGPL-3.0.
"""Channel thumbnail rules: who uploads, who downloads, what counts as an image."""

import uuid
import warnings

from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files import File
from django.db import transaction
from django.db.models import Max
from django.utils.text import slugify
from PIL import Image, UnidentifiedImageError

from apps.jrc_texts.services import can_edit_text
from apps.members.models import WorkspaceMembership
from apps.members.permissions import can_publish_directly

from .models import ChannelThumbnail

MAX_BYTES = 20 * 1024 * 1024
# Bound decoded dimensions as well as compressed bytes to limit memory usage.
MAX_PIXELS = 100_000_000
# Pillow format -> (stored content type, canonical extension). The sniffed format decides
# the stored name, so a file is never served under a misleading suffix.
FORMATS = {"JPEG": ("image/jpeg", "jpg"), "PNG": ("image/png", "png"), "WEBP": ("image/webp", "webp")}
EXTENSIONS = {"jpg", "jpeg", "png", "webp"}
DECLARED_TYPES = {"image/jpeg", "image/jpg", "image/pjpeg", "image/png", "image/webp"}
TYPE_ERROR = "Upload a JPG, PNG or WebP image."


def member(workspace, user):
    """The active membership, or None: callers answer 404 so other workspaces stay invisible."""
    if not user.is_authenticated or not user.is_active or workspace.is_archived:
        return None
    return (
        WorkspaceMembership.objects.filter(
            user=user, workspace=workspace, workspace__organization__deleted_at__isnull=True
        )
        .select_related("custom_role")
        .first()
    )


def can_upload(post, membership):
    """Owner, manager and editor; a contributor only on their own content (same as captions)."""
    if not membership or membership.workspace_id != post.workspace_id:
        return False
    role = membership.workspace_role
    if role not in {"owner", "manager", "editor", "contributor"}:
        return False
    if role == "contributor" and post.author_id != membership.user_id:
        return False
    return can_edit_text(post, membership)


def can_download(membership):
    """The team and the client download; a viewer only sees the thumbnail."""
    return can_publish_directly(membership)


def validate_image(upload):
    """Check the declared type, the size and the content itself; return what the file really is."""
    if upload is None:
        raise ValidationError("Choose an image to upload.")
    name = (upload.name or "").rsplit("/", 1)[-1]
    extension = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    declared = (getattr(upload, "content_type", "") or "").lower()
    if extension not in EXTENSIONS and declared not in DECLARED_TYPES:
        raise ValidationError(TYPE_ERROR)
    if upload.size > MAX_BYTES:
        raise ValidationError("The image is larger than 20 MB.")
    if not upload.size:
        raise ValidationError(TYPE_ERROR)
    try:
        upload.seek(0)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", Image.DecompressionBombWarning)
            with Image.open(upload) as image:
                image_format = image.format
                width, height = image.size
                if image_format not in FORMATS:
                    raise ValidationError(TYPE_ERROR)
                if width * height > MAX_PIXELS:
                    raise ValidationError("The image has too many pixels.")
                image.verify()
            # JPEG.verify() alone does not decode the pixels and can accept a
            # truncated upload. Check that the whole image can actually be read.
            upload.seek(0)
            with Image.open(upload) as image:
                image.load()
    except ValidationError:
        raise
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, SyntaxError, ValueError):
        raise ValidationError(TYPE_ERROR) from None
    finally:
        upload.seek(0)
    content_type, canonical = FORMATS[image_format]
    return {"content_type": content_type, "extension": canonical, "width": width, "height": height}


def upload_thumbnail(post, platform_post, user, upload, membership):
    """Store a new version; the newest version is the channel's current thumbnail."""
    if platform_post.post_id != post.pk:
        raise ValidationError("Choose a channel of this content.")
    if not can_upload(post, membership):
        raise PermissionDenied("You cannot upload thumbnails for this content.")
    found = validate_image(upload)
    from apps.composer.models import Post

    with transaction.atomic():
        # Same lock order as channel removal: the parent first, then the copy's history.
        Post.objects.select_for_update().get(pk=post.pk)
        if not post.platform_posts.filter(pk=platform_post.pk).exists():
            raise ValidationError("Choose a channel of this content.")
        last = ChannelThumbnail.objects.filter(platform_post=platform_post).aggregate(n=Max("number"))["n"] or 0
        thumbnail = ChannelThumbnail(
            id=uuid.uuid4(),
            post=post,
            platform_post=platform_post,
            number=last + 1,
            original_name=(upload.name or "")[-255:],
            content_type=found["content_type"],
            size=upload.size,
            width=found["width"],
            height=found["height"],
            uploaded_by=user,
        )
        thumbnail.image.save(f"{thumbnail.id}.{found['extension']}", File(upload), save=False)
        thumbnail.save()
    return thumbnail


def download_filename(thumbnail):
    account = thumbnail.platform_post.social_account
    parts = (
        slugify(thumbnail.post.title or "", allow_unicode=False)[:80] or "content",
        slugify(f"{account.get_platform_display()}-{account.account_name}", allow_unicode=False)[:80] or "channel",
        f"thumbnail-v{thumbnail.number}",
    )
    return "-".join(parts) + "." + thumbnail.image.name.rsplit(".", 1)[-1]
