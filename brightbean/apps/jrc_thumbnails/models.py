# JR Content: přidáno 2026-11-04 (blok P1-B10), AGPL-3.0.
"""One thumbnail image per channel copy, with every uploaded version kept (P1-B10)."""

import uuid

from django.conf import settings
from django.db import models
from django.db.models import Q


def thumbnail_path(instance, filename):
    """Private key in the jrc-app bucket; read only through the session-checked file gateway (P1-B03)."""
    extension = filename.rsplit(".", 1)[-1].lower()
    return f"jrc_thumbnails/{instance.post.workspace_id}/{instance.platform_post_id}/{instance.id}.{extension}"


class ChannelThumbnail(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # Deleting the whole content removes its thumbnails; removing one channel copy is
    # refused while it has thumbnails (same rule as caption history and video).
    post = models.ForeignKey("composer.Post", on_delete=models.CASCADE, related_name="jrc_thumbnails")
    platform_post = models.ForeignKey("composer.PlatformPost", on_delete=models.RESTRICT, related_name="jrc_thumbnails")
    number = models.PositiveIntegerField()
    image = models.FileField(upload_to=thumbnail_path, max_length=255)
    original_name = models.CharField(max_length=255, blank=True, default="")
    content_type = models.CharField(max_length=20)
    size = models.PositiveBigIntegerField()
    width = models.PositiveIntegerField()
    height = models.PositiveIntegerField()
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-number"]
        constraints = [
            models.UniqueConstraint(fields=["platform_post", "number"], name="jrc_thumbnail_version_number"),
            models.CheckConstraint(condition=Q(number__gte=1), name="jrc_thumbnail_positive_number"),
            models.CheckConstraint(
                condition=Q(content_type__in=["image/jpeg", "image/png", "image/webp"]),
                name="jrc_thumbnail_content_type",
            ),
        ]

    def __str__(self):
        return f"Thumbnail v{self.number} for {self.platform_post_id}"
