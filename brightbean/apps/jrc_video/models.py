import uuid

from django.conf import settings
from django.db import models


class VideoProject(models.Model):
    workspace = models.OneToOneField("workspaces.Workspace", on_delete=models.CASCADE, primary_key=True)
    ff_project_id = models.UUIDField(unique=True)
    synced_at = models.DateTimeField()


class VideoAccount(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, primary_key=True)
    ff_user_id = models.UUIDField(unique=True)
    synced_at = models.DateTimeField()


class VideoSyncRequest(models.Model):
    """Durable retry marker; contains neither credentials nor remote response bodies."""

    workspace = models.OneToOneField("workspaces.Workspace", on_delete=models.CASCADE, primary_key=True)
    requested_at = models.DateTimeField(auto_now=True)
    last_error = models.CharField(max_length=255, blank=True)


class VideoAccountSyncRequest(models.Model):
    """Retry marker for an account outside any workspace sync (e.g. blocked without memberships)."""

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, primary_key=True)
    requested_at = models.DateTimeField(auto_now=True)
    last_error = models.CharField(max_length=255, blank=True)


class VideoAccountRevocation(models.Model):
    """Deletion retry independent of the user FK, so it survives the cascade."""

    source_user_id = models.UUIDField(primary_key=True)
    requested_at = models.DateTimeField(auto_now_add=True)
    last_error = models.CharField(max_length=255, blank=True)


class VideoAsset(models.Model):
    """The FreeFrame video of one channel copy (ADR-002): one asset per PlatformPost."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    platform_post = models.OneToOneField("composer.PlatformPost", on_delete=models.CASCADE, related_name="jrc_video")
    ff_asset_id = models.UUIDField(unique=True)
    final_version = models.ForeignKey(
        "VideoVersion", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    # F5-B08: who marked it and when ("Final for publishing"); not in the ADR table.
    final_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    final_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class VideoVersion(models.Model):
    """v1, v2 ... of a channel video. FreeFrame is the source of truth; this is a copy."""

    class Status(models.TextChoices):
        UPLOADING = "uploading", "Waiting for upload"
        PROCESSING = "processing", "Converting"
        READY = "ready", "Ready"
        FAILED = "failed", "Error"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    video = models.ForeignKey(VideoAsset, on_delete=models.CASCADE, related_name="versions")
    number = models.PositiveIntegerField()
    ff_version_id = models.UUIDField(unique=True)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    uploaded_at = models.DateTimeField(auto_now_add=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.UPLOADING)
    duration_s = models.FloatField(null=True, blank=True)
    fps = models.FloatField(null=True, blank=True)
    width = models.PositiveIntegerField(null=True, blank=True)
    height = models.PositiveIntegerField(null=True, blank=True)
    # Added by F5-B05 for the "continue upload" prompt (not in the ADR table).
    original_filename = models.CharField(max_length=255, blank=True)
    size_bytes = models.PositiveBigIntegerField(default=0)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["video", "number"], name="jrc_video_version_number_uniq")]
        ordering = ["-number"]
