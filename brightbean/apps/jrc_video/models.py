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
