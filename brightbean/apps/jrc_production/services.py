from django.core.exceptions import ValidationError
from django.db import transaction

from apps.composer.models import PlatformPost, Post
from apps.jrc_channels.models import ChannelProfile

from .models import PipelineStage, PostProduction, ProductionStage

_STATUS_TO_STAGE = {
    "pending_review": PipelineStage.REVIEW,
    "pending_client": PipelineStage.REVIEW,
    "changes_requested": PipelineStage.REVIEW,
    "rejected": PipelineStage.REVIEW,
    "on_hold": PipelineStage.REVIEW,
    "approved": PipelineStage.APPROVED,
    "scheduled": PipelineStage.READY,
    "publishing": PipelineStage.READY,
    "failed": PipelineStage.READY,
    "published": PipelineStage.PUBLISHED,
    "partially_published": PipelineStage.PUBLISHED,
}


def pipeline_stage(post):
    """Derive the eight-stage pipeline from Brightbean's aggregate status (ADR-002 b1)."""
    status = post.status
    if status == PlatformPost.Status.DRAFT:
        try:
            return post.jrc_production.stage
        except PostProduction.DoesNotExist:
            # Existing upstream posts have no extension. Reading must not write data.
            return ProductionStage.IDEA
    return _STATUS_TO_STAGE[status]


@transaction.atomic
def create_content(workspace, title, user):
    """Create local content and one draft variant for each enabled client channel.

    No enabled channel means the upstream lists would hide the content, so reject
    creation before writing anything. Authorization belongs to the calling view/API.
    """
    channels = list(
        ChannelProfile.objects.filter(social_account__workspace=workspace, is_enabled=True)
        .select_related("social_account")
        .order_by("created_at", "social_account_id")
    )
    if not channels:
        raise ValidationError("Enable at least one channel before creating content.")

    post = Post(workspace=workspace, title=title, author=user)
    post.full_clean()
    post.save()
    PostProduction.objects.create(post=post, stage_changed_by=user)
    PlatformPost.objects.bulk_create(
        [PlatformPost(post=post, social_account=channel.social_account) for channel in channels]
    )
    return post
