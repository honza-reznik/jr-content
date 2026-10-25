from urllib.parse import urlsplit

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from apps.approvals import services as approvals
from apps.composer.models import PlatformPost, Post
from apps.composer.services import sync_post_scheduled_at
from apps.jrc_channels.models import ChannelProfile
from apps.members.models import WorkspaceMembership
from apps.members.note_visibility import note_audience
from apps.members.permissions import can_publish_directly

from .models import ManualPublication, PipelineStage, PostProduction, ProductionStage, StageChange

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
    creation before writing anything. Check authorization here for every caller.
    """
    membership = content_membership(workspace, user)
    if not can_create_content(membership):
        raise PermissionDenied("You cannot create content for this client.")
    title = title.strip()
    if not title:
        raise ValidationError("Enter a content title.")
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


def content_membership(workspace, user):
    if not user or not user.is_authenticated or not user.is_active:
        raise PermissionDenied("Sign in to access content.")
    membership = (
        WorkspaceMembership.objects.filter(user=user, workspace=workspace, workspace__is_archived=False)
        .select_related("custom_role")
        .first()
    )
    if membership is None:
        raise PermissionDenied("You do not have access to this client.")
    return membership


def is_client(membership):
    return membership.custom_role_id is None and membership.workspace_role == "client"


def can_create_content(membership):
    # Local content only. Do not grant clients upstream publishing/media permissions.
    return is_client(membership) or membership.effective_permissions.get("create_posts", False)


def can_decide(post, membership, statuses=None):
    """May this member approve or request changes right now (F7-B02)?

    One rule for both decisions, used by ``available_moves`` and the Approval card. Single-stage
    workspaces let anyone with ``approve_posts`` decide on content in review. In the two-stage
    mode (internal, then client) the team decides while the content waits for the team and the
    client only after the team has approved it.
    """
    if statuses is None:
        statuses = {pp.status for pp in post.platform_posts.all()}
    if not membership.effective_permissions.get("approve_posts", False):
        return False
    if not statuses or not statuses <= {"pending_review", "pending_client", "approved"}:
        return False
    pending = statuses - {"approved"}
    if not pending:
        return False
    if post.workspace.approval_workflow_mode != "required_internal_and_client":
        return True
    # Custom permissions do not change which side of the workflow a member is on.
    return pending == ({"pending_client"} if membership.workspace_role == "client" else {"pending_review"})


def available_moves(post, membership):
    """Offer exactly the transitions the service will authorize for this snapshot."""
    children = list(post.platform_posts.all())
    if not children:
        return []
    statuses = {pp.status for pp in children}
    permissions = membership.effective_permissions
    can_edit = can_create_content(membership) and (
        post.author_id == membership.user_id or permissions.get("edit_others_posts", False)
    )
    moves = []
    if can_edit and statuses <= {"draft"}:
        moves.extend(stage for stage in ProductionStage.values if stage != pipeline_stage(post))
    if can_edit and statuses <= {"draft", "changes_requested", "rejected", "approved", "scheduled"}:
        if statuses <= {"draft", "changes_requested", "rejected"}:
            moves.append(PipelineStage.REVIEW)
        else:
            moves.append(ProductionStage.EDITING)
    if can_decide(post, membership, statuses):
        moves.append(PipelineStage.APPROVED)
    if permissions.get("publish_directly", False) and statuses <= {"approved"}:
        moves.append(PipelineStage.READY)
    return moves


@transaction.atomic
def request_content_changes(post, user, comment, *, expected_stage):
    """Send content in review back to the team with a required comment (F7-B02).

    Same authorization as approving (``can_decide``). The decision itself, its history record
    and the notification to the author come from Brightbean's ``request_changes``; the pipeline
    stage stays Review until the team resubmits.
    """
    post = Post.objects.select_for_update(of=("self",)).select_related("workspace").get(pk=post.pk)
    membership = content_membership(post.workspace, user)
    children = list(post.platform_posts.select_for_update(of=("self",)))
    if pipeline_stage(post) != expected_stage:
        raise ValidationError("This content changed. Refresh the page and try again.")
    if not membership.effective_permissions.get("approve_posts", False):
        raise PermissionDenied("You cannot request changes for this content.")
    statuses = {pp.status for pp in children}
    if not statuses & {"pending_review", "pending_client"}:
        raise ValidationError("This content is not waiting for a decision.")
    if not can_decide(post, membership, statuses):
        raise PermissionDenied("You cannot request changes at this step.")
    comment = (comment or "").strip()
    if not comment:
        raise ValidationError("Tell the team what to change before sending the request.")
    if len(comment) > 5000:
        raise ValidationError("Keep the request under 5,000 characters.")
    approvals.request_changes(post, user, post.workspace, comment)
    post.refresh_from_db()
    return post


@transaction.atomic
def move_content(post, stage, user, *, expected_stage, scheduled_at=None):
    """Serialize changes, authorize the actor and delegate editorial decisions to Brightbean."""
    post = Post.objects.select_for_update(of=("self",)).select_related("workspace").get(pk=post.pk)
    membership = content_membership(post.workspace, user)
    children = list(post.platform_posts.select_for_update(of=("self",)).select_related("social_account__jrc_channel"))
    previous = pipeline_stage(post)
    if previous != expected_stage:
        raise ValidationError("This content changed. Refresh the board and try again.")
    if stage not in PipelineStage.values:
        raise ValidationError("Choose a valid stage.")
    if stage not in available_moves(post, membership):
        raise PermissionDenied("You cannot make this move. Check your permissions and the current stage.")
    if stage in ProductionStage.values:
        for child in children:
            if child.status != "draft":
                from apps.composer.services import transition_platform_post

                transition_platform_post(child, "draft")
        production, _ = PostProduction.objects.get_or_create(post=post)
        production.stage = stage
        production.stage_changed_at = timezone.now()
        production.stage_changed_by = user
        production.save()
    elif stage == PipelineStage.REVIEW:
        approvals.submit_for_review(post, user, post.workspace)
    elif stage == PipelineStage.APPROVED:
        approvals.approve_post(post, user, post.workspace)
    elif stage == PipelineStage.READY:
        if scheduled_at is None or timezone.is_naive(scheduled_at) or scheduled_at <= timezone.now():
            raise ValidationError("Choose a future publication date and time.")
        for child in children:
            # Manual JR channels wait in scheduled until a human marks publication.
            # The upstream scheduling service assumes live publishing and rejects
            # mandatory-review workspaces even after approval. Keep its state
            # machine and aggregate scheduling service, without that direct gate.
            profile = getattr(child.social_account, "jrc_channel", None)
            if profile is None or profile.kind not in {ChannelProfile.Kind.MANUAL, ChannelProfile.Kind.CUSTOM}:
                raise ValidationError("The board can plan manual channels only.")
            child.transition_to("scheduled")
            child.scheduled_at = scheduled_at
            child.save(update_fields=[*PlatformPost.TRANSITION_FIELDS, "scheduled_at", "updated_at"])
        sync_post_scheduled_at(post)
    post.refresh_from_db()
    current = pipeline_stage(post)
    if current != previous:
        StageChange.objects.create(
            post=post, from_stage=previous, to_stage=current, user=user, **note_audience(post.workspace, user)
        )
    return post


@transaction.atomic
def mark_published_manually(post, platform_post_id, user, *, post_url=""):
    """Record a team's manual publication for one approved channel copy."""
    from apps.jrc_video.models import VideoAsset, VideoVersion

    post = Post.objects.select_for_update(of=("self",)).select_related("workspace").get(pk=post.pk)
    membership = content_membership(post.workspace, user)
    if not can_publish_directly(membership):
        raise PermissionDenied("You cannot mark content as published.")
    try:
        child = (
            PlatformPost.objects.select_for_update(of=("self",))
            .select_related("social_account")
            .get(pk=platform_post_id, post=post)
        )
    except (PlatformPost.DoesNotExist, ValueError, TypeError):
        raise ValidationError("Choose a channel of this content.") from None
    if child.status not in {PlatformPost.Status.APPROVED, PlatformPost.Status.SCHEDULED}:
        raise ValidationError("Only approved or scheduled content can be marked as published.")
    if not (child.platform_specific_caption or child.post.caption or "").strip():
        raise ValidationError("Add a caption before marking this content as published.")
    video = VideoAsset.objects.filter(platform_post=child).select_related("final_version").first()
    if video and (not video.final_version_id or video.final_version.status != VideoVersion.Status.READY):
        raise ValidationError("Mark a converted video as final before publishing this channel.")
    post_url = (post_url or "").strip()
    if post_url:
        parsed = urlsplit(post_url)
        if (
            len(post_url) > 2000
            or parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
        ):
            raise ValidationError("The post link must be a valid HTTPS address.")
    if hasattr(child, "jrc_publication"):
        raise ValidationError("This channel is already marked as published.")

    previous = pipeline_stage(post)
    publication = ManualPublication.objects.create(
        platform_post=child, published_by=user, published_at=timezone.now(), post_url=post_url
    )
    child.transition_to(PlatformPost.Status.PUBLISHING)
    child.transition_to(PlatformPost.Status.PUBLISHED)
    child.save(update_fields=[*PlatformPost.TRANSITION_FIELDS, "updated_at"])
    from apps.composer.services import sync_post_scheduled_at

    sync_post_scheduled_at(post)
    post.refresh_from_db()
    current = pipeline_stage(post)
    if current != previous:
        StageChange.objects.create(
            post=post, from_stage=previous, to_stage=current, user=user, **note_audience(post.workspace, user)
        )
    return publication
