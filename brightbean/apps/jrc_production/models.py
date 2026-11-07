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
    actor_role = models.CharField(max_length=20, blank=True, default="")
    note_visibility = models.CharField(
        max_length=10, choices=[("internal", "Internal"), ("external", "External")], default="internal"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]
        indexes = [models.Index(fields=["post", "created_at"], name="jrc_stage_post_created_idx")]


class ShotlistItem(models.Model):
    """One line of the content's shotlist: a shot or asset to produce (P1-B09)."""

    class Kind(models.TextChoices):
        ITEM = "item", "Item"
        # Every content has exactly one; it is checked off by hand and cannot be deleted.
        THUMBNAIL = "thumbnail", "Thumbnail photo"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    post = models.ForeignKey("composer.Post", on_delete=models.CASCADE, related_name="jrc_shotlist_items")
    kind = models.CharField(max_length=20, choices=Kind.choices, default=Kind.ITEM)
    text = models.CharField(max_length=300)
    note = models.TextField(blank=True, default="", max_length=1000)
    is_done = models.BooleanField(default=False)
    position = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    class Meta:
        ordering = ["position", "created_at", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["post"],
                condition=models.Q(kind="thumbnail"),
                name="jrc_shotlist_one_thumbnail_per_post",
            ),
            models.CheckConstraint(condition=models.Q(kind__in=["item", "thumbnail"]), name="jrc_shotlist_kind_valid"),
        ]
        indexes = [models.Index(fields=["post", "position"], name="jrc_shotlist_post_pos_idx")]


class ShotlistChange(models.Model):
    """Shotlist history for the History tab. Keeps the text, so deleted items stay readable."""

    class Action(models.TextChoices):
        ADDED = "added", "Added"
        DONE = "done", "Checked off"
        UNDONE = "undone", "Unchecked"
        DELETED = "deleted", "Removed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    post = models.ForeignKey("composer.Post", on_delete=models.CASCADE, related_name="jrc_shotlist_changes")
    # No foreign key: the history row must outlive a deleted item.
    item_id = models.UUIDField(null=True, blank=True)
    item_text = models.CharField(max_length=300)
    action = models.CharField(max_length=20, choices=Action.choices)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]
        indexes = [models.Index(fields=["post", "created_at"], name="jrc_shotlist_chg_post_idx")]


class ManualPublication(models.Model):
    platform_post = models.OneToOneField(
        "composer.PlatformPost", on_delete=models.CASCADE, related_name="jrc_publication"
    )
    published_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    published_at = models.DateTimeField()
    post_url = models.URLField(max_length=2000, blank=True, default="")


class ContentRename(models.Model):
    """Rename history for the History tab (P1-B38). Keeps both names, so it stays readable later."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    post = models.ForeignKey("composer.Post", on_delete=models.CASCADE, related_name="jrc_renames")
    old_title = models.CharField(max_length=255, blank=True, default="")
    new_title = models.CharField(max_length=255)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]
        indexes = [models.Index(fields=["post", "created_at"], name="jrc_rename_post_idx")]
