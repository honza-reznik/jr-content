import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone


class ProductionStage(models.TextChoices):
    IDEA = "idea", "Idea"
    SCRIPT = "script", "Script"
    SHOOTING = "shooting", "Shooting"
    EDITING = "editing", "Editing"


class PipelineStage(models.TextChoices):
    IDEA = "idea", "Idea"
    SCRIPT = "script", "Script"
    SHOOTING = "shooting", "Shooting"
    EDITING = "editing", "Editing"
    REVIEW = "review", "Review"
    APPROVED = "approved", "Approved"
    READY = "ready", "Ready to publish"
    PUBLISHED = "published", "Published (manually)"


class PostProduction(models.Model):
    """Only production is stored here; editorial status belongs to Brightbean."""

    post = models.OneToOneField(
        "composer.Post",
        on_delete=models.CASCADE,
        primary_key=True,
        related_name="jrc_production",
    )
    stage = models.CharField(max_length=20, choices=ProductionStage.choices, default=ProductionStage.IDEA)
    stage_changed_at = models.DateTimeField(default=timezone.now)
    stage_changed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(stage__in=ProductionStage.values), name="jrc_production_stage_valid"
            ),
        ]


class StageChange(models.Model):
    """Production history; Brightbean's ApprovalAction remains the approval log."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    post = models.ForeignKey("composer.Post", on_delete=models.CASCADE, related_name="jrc_stage_changes")
    from_stage = models.CharField(max_length=20, choices=PipelineStage.choices)
    to_stage = models.CharField(max_length=20, choices=PipelineStage.choices)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    note = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]
        indexes = [models.Index(fields=["post", "created_at"], name="jrc_stage_post_created_idx")]


class ManualPublication(models.Model):
    platform_post = models.OneToOneField(
        "composer.PlatformPost", on_delete=models.CASCADE, related_name="jrc_publication"
    )
    published_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    published_at = models.DateTimeField()
    post_url = models.URLField(max_length=2000, blank=True, default="")
