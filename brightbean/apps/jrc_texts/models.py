import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q


class TextDocument(models.Model):
    class Kind(models.TextChoices):
        SCRIPT = "script", "Script"
        CAPTION = "caption", "Caption"

    class Format(models.TextChoices):
        RICH = "rich", "Rich text"
        PLAIN = "plain", "Plain text"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace = models.ForeignKey("workspaces.Workspace", on_delete=models.CASCADE, related_name="jrc_texts")
    post = models.ForeignKey("composer.Post", on_delete=models.CASCADE, related_name="jrc_texts")
    kind = models.CharField(max_length=10, choices=Kind.choices)
    platform_post = models.ForeignKey(
        "composer.PlatformPost", on_delete=models.CASCADE, null=True, blank=True, related_name="jrc_texts"
    )
    format = models.CharField(max_length=10, choices=Format.choices, default=Format.RICH)
    created_at = models.DateTimeField(auto_now_add=True)

    def clean(self):
        super().clean()
        if self.post_id and self.workspace_id != self.post.workspace_id:
            raise ValidationError({"workspace": "The text must belong to the post's workspace."})
        if self.platform_post_id and self.platform_post.post_id != self.post_id:
            raise ValidationError({"platform_post": "The channel variant must belong to this post."})

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["post", "kind"], condition=Q(kind="script"), name="jrc_text_one_script"),
            models.UniqueConstraint(fields=["platform_post"], condition=Q(kind="caption"), name="jrc_text_one_caption"),
            models.CheckConstraint(
                condition=(
                    Q(kind="script", platform_post__isnull=True) | Q(kind="caption", platform_post__isnull=False)
                ),
                name="jrc_text_document_kind",
            ),
            models.CheckConstraint(condition=Q(format__in=["rich", "plain"]), name="jrc_text_document_format"),
        ]


class TextVersion(models.Model):
    class State(models.TextChoices):
        DRAFT = "draft", "Draft"
        FINAL = "final", "Saved"

    class Source(models.TextChoices):
        EDITOR = "editor", "Editor"
        API = "api", "API"
        MCP = "mcp", "MCP"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    document = models.ForeignKey(TextDocument, on_delete=models.CASCADE, related_name="versions")
    number = models.PositiveIntegerField()
    state = models.CharField(max_length=10, choices=State.choices, default=State.DRAFT)
    content = models.JSONField()
    text = models.TextField(blank=True)
    char_count = models.PositiveIntegerField(null=True, blank=True)
    based_on = models.ForeignKey("self", on_delete=models.SET_NULL, null=True, blank=True, related_name="successors")
    source = models.CharField(max_length=10, choices=Source.choices, default=Source.EDITOR)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="jrc_text_versions"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    finalized_at = models.DateTimeField(null=True, blank=True)
    draft_owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="jrc_text_drafts"
    )
    draft_heartbeat_at = models.DateTimeField(null=True, blank=True)
    # Optimistic lock for autosave/save from two tabs sharing the same draft ID.
    revision = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["number"]
        constraints = [
            models.UniqueConstraint(fields=["document", "number"], name="jrc_text_version_number"),
            models.UniqueConstraint(fields=["document"], condition=Q(state="draft"), name="jrc_text_one_draft"),
            models.CheckConstraint(condition=Q(number__gte=1), name="jrc_text_positive_number"),
            models.CheckConstraint(
                condition=Q(state="draft", finalized_at__isnull=True) | Q(state="final", finalized_at__isnull=False),
                name="jrc_text_version_state",
            ),
            models.CheckConstraint(condition=Q(source__in=["editor", "api", "mcp"]), name="jrc_text_version_source"),
        ]
