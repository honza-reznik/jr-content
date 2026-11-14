from urllib.parse import urlsplit

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from apps.approvals import services as approvals
from apps.approvals.models import PostComment
from apps.composer.models import PlatformPost, Post
from apps.composer.services import children_are_locked, remove_platform_posts, sync_post_scheduled_at
from apps.jrc_channels.models import ChannelProfile
from apps.members.models import WorkspaceMembership
from apps.members.note_visibility import note_audience
from apps.members.permissions import (
    can_publish_directly,
    can_schedule,
    can_unschedule,
    content_delete_blocker,
    is_planned,
    release_blocker,
    review_available,
)

from .models import ContentRename, ManualPublication, PipelineStage, PostProduction, ProductionStage, StageChange

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


MAX_DESCRIPTION = 5000


def initial_stages(membership):
    """Stages new content may start in for this member (P2-B03): the moves ``available_moves`` offers
    a draft its author just created, i.e. any production stage, Review where the workspace has a review
    step and Ready to publish for a planner where drafts may go out. Approved and Published are never
    a starting point: they need an approval or a publication. ``create_content`` still makes the move
    through ``move_content``, so channels and the date are checked by the same rule as on the board.
    """
    if not can_create_content(membership):
        return []
    workspace = membership.workspace
    stages = list(ProductionStage.values)
    if review_available(workspace):
        stages.append(PipelineStage.REVIEW)
    if can_schedule(membership) and not release_blocker(workspace, PlatformPost.Status.DRAFT):
        stages.append(PipelineStage.READY)
    return stages


@transaction.atomic
def create_content(
    workspace,
    title,
    user,
    *,
    social_account_ids=(),
    description="",
    stage=ProductionStage.IDEA,
    publish_at=None,
):
    """Create content with only explicitly selected enabled channels (none by default).

    P2-B03: the one creation path of the quick idea, the New idea window and the New content page.
    ``description`` becomes version 1 of the Concept. ``stage`` (default Idea) is reached by a move
    from Idea under the board's rules (``initial_stages`` / ``move_content``), so Review needs a
    channel and Ready a planner, a channel and a future ``publish_at``. For the other stages
    ``publish_at`` is stored as the proposed publication date. Nothing is saved when any step fails.
    """
    from apps.composer.services import create_post

    membership = content_membership(workspace, user)
    if not can_create_content(membership):
        raise PermissionDenied("You cannot create content for this client.")
    title = title.strip()
    if not title:
        raise ValidationError("Enter a content title.")
    Post(workspace=workspace, title=title, author=user).full_clean()
    description = (description or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if len(description) > MAX_DESCRIPTION:
        raise ValidationError(f"Keep the description under {MAX_DESCRIPTION:,} characters.")
    stage = stage or ProductionStage.IDEA
    if stage not in initial_stages(membership):
        raise ValidationError("Choose a stage this content can start in.")
    if publish_at is not None and timezone.is_naive(publish_at):
        raise ValidationError("Choose a valid publication date and time.")
    ids = {str(value) for value in social_account_ids}
    channels = list(
        ChannelProfile.objects.filter(social_account__workspace=workspace, is_enabled=True)
        .select_related("social_account")
        .order_by("created_at", "social_account_id")
    )
    selected = [channel.social_account for channel in channels if str(channel.social_account_id) in ids]
    if {str(account.pk) for account in selected} != ids:
        raise ValidationError("Choose enabled channels of this client.")
    post = create_post(
        workspace=workspace,
        title=title,
        author=user,
        caption="",
        social_accounts=selected,
        proposed_publish_at=None if stage == PipelineStage.READY else publish_at,
    )
    PostProduction.objects.create(post=post, stage_changed_by=user)
    if description:
        from apps.jrc_texts.services import create_initial_concept

        create_initial_concept(post, user, description)
    if stage != ProductionStage.IDEA:
        try:
            post = move_content(
                post,
                stage,
                user,
                expected_stage=ProductionStage.IDEA,
                scheduled_at=publish_at if stage == PipelineStage.READY else None,
            )
        except PermissionDenied as exc:
            raise ValidationError(str(exc) or "You cannot start content in this stage.") from exc
    return post


def can_rename_content(post, membership):
    """Renaming is editing the content: your own with create rights, someone else's with edit-others rights."""
    permissions = membership.effective_permissions
    return permissions.get("create_posts", False) and (
        post.author_id == membership.user_id or permissions.get("edit_others_posts", False)
    )


def record_title_change(post, old_title, user):
    """History row for a title written by another path (composer, REST PATCH); no-op when unchanged."""
    if old_title is not None and old_title != post.title:
        ContentRename.objects.create(post=post, old_title=old_title, new_title=post.title, user=user)


def rename_content(post, title, user):
    """Change the content title (P1-B38): checked, written to History and, where the title goes out, re-reviewed.

    The title is what a channel without its own title publishes, so it is reviewed content: the change
    goes through ``approvals.edits`` like every other edit and sends approved copies back to review in
    the modes that require approval. Returns the copies sent back to review, or ``None`` (nothing written)
    when the name is unchanged.
    """
    from apps.approvals.edits import return_edited_to_review, reviewed_content

    with transaction.atomic():
        locked = Post.objects.select_for_update(of=("self",)).select_related("workspace").get(pk=post.pk)
        membership = content_membership(locked.workspace, user)
        if not can_rename_content(locked, membership):
            raise PermissionDenied("You cannot rename this content.")
        # The snapshot locks the copies (publisher's order, P1-B37) before their state is read.
        before = reviewed_content(locked)
        if children_are_locked(locked.platform_posts.only("id", "status")):
            raise ValidationError("This content has already published and can't be renamed.")
        title = (title or "").strip()
        if not title:
            raise ValidationError("Enter a content title.")
        Post(workspace=locked.workspace, title=title, author=locked.author).full_clean()
        if title == locked.title:
            return None
        old_title = locked.title
        locked.title = title
        locked.save(update_fields=["title", "updated_at"])
        ContentRename.objects.create(post=locked, old_title=old_title, new_title=title, user=user)
        return return_edited_to_review(locked, before)


def can_manage_channels(post, membership):
    permissions = membership.effective_permissions
    return permissions.get("create_posts", False) and (
        post.author_id == membership.user_id or permissions.get("edit_others_posts", False)
    )


def _lock_channel_content(post, user):
    post = Post.objects.select_for_update(of=("self",)).select_related("workspace").get(pk=post.pk)
    membership = content_membership(post.workspace, user)
    if not can_manage_channels(post, membership):
        raise PermissionDenied("You cannot change channels for this content.")
    if post.platform_posts.exclude(status="draft").exists():
        raise ValidationError("Move this content back to Editing before changing channels.")
    return post


@transaction.atomic
def add_content_channel(post, account_id, user, output_format=""):
    """Add one output (channel + format) to the content; the format defaults to the platform's first.

    P2-B14: a channel may carry several outputs of one content, one per format (YouTube · Video and
    YouTube · Shorts). Adding an output that already exists returns it instead of a duplicate.
    """
    from apps.composer import output_formats
    from apps.composer.services import add_platform_post

    post = _lock_channel_content(post, user)
    channel = (
        ChannelProfile.objects.select_for_update(of=("self",))
        .select_related("social_account")
        .filter(social_account__workspace=post.workspace, is_enabled=True, social_account_id=account_id)
        .first()
    )
    if channel is None:
        raise ValidationError("Choose an enabled channel of this client.")
    platform = channel.social_account.platform
    output_format = output_format or output_formats.default_format(platform)
    if not output_formats.is_allowed(platform, output_format):
        raise ValidationError("Choose a format this channel offers.")
    existing = post.platform_posts.filter(social_account_id=account_id, output_format=output_format).first()
    if existing:
        return existing
    first_output = not post.platform_posts.exists()
    variant = add_platform_post(post, channel.social_account, output_format=output_format)
    if first_output:
        # The shotlist written before the content had an output is the list of its first format, and
        # stays with that format when more formats are added later (P2-B14).
        from .models import ShotlistItem

        ShotlistItem.objects.filter(post=post, kind=ShotlistItem.Kind.ITEM, output_format="").update(
            output_format=output_format
        )
    post.save(update_fields=["updated_at"])
    return variant


def addable_outputs(post):
    """Enabled channel + format pairs the content does not have yet, for the "+" of the output tags."""
    from apps.composer import output_formats

    taken = set(post.platform_posts.values_list("social_account_id", "output_format"))
    options = []
    channels = (
        ChannelProfile.objects.filter(social_account__workspace=post.workspace, is_enabled=True)
        .select_related("social_account")
        .order_by("social_account__account_name", "social_account_id")
    )
    for channel in channels:
        account = channel.social_account
        for output_format, name in output_formats.formats_for(account.platform).items():
            if (account.pk, output_format) in taken:
                continue
            platform_name = (
                channel.display_name or account.account_name
                if account.platform == "custom"
                else account.get_platform_display()
            )
            options.append(
                {
                    "value": f"{account.pk}:{output_format}",
                    "account": account,
                    "output_format": output_format,
                    "channel_name": channel.display_name or account.account_name,
                    "label": f"{platform_name} · {name}",
                }
            )
    return options


def parse_output_choice(value):
    """``"<account id>:<format>"`` from the output tag form; a bare account id means the default format."""
    account_id, _, output_format = str(value or "").partition(":")
    return account_id, output_format


@transaction.atomic
def remove_content_channel(post, platform_post_id, user):
    """Remove one channel copy (JR detail and REST); the one deletion rule applies (P1-B37)."""
    post = _lock_channel_content(post, user)
    variants = post.platform_posts.filter(pk=platform_post_id)
    if not variants.exists():
        raise ValidationError("Choose a channel of this content.")
    blocker = content_delete_blocker(content_membership(post.workspace, user), post, list(variants))
    if blocker:
        raise PermissionDenied(blocker)
    remove_platform_posts(variants)


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
    workspaces let team members with ``approve_posts`` decide on content in review. In the two-stage
    mode (internal, then client) the team decides while the content waits for the team and the
    client only after the team has approved it.
    """
    if statuses is None:
        statuses = {pp.status for pp in post.platform_posts.all()}
    from apps.members.permissions import can_decide_at_step

    if not statuses or not statuses <= {"pending_review", "pending_client", "approved"}:
        return False
    pending = statuses - {"approved"}
    return bool(pending) and all(can_decide_at_step(membership, step) for step in pending)


def available_moves(post, membership):
    """Offer exactly the transitions the service will authorize for this snapshot."""
    children = list(post.platform_posts.all())
    statuses = {pp.status for pp in children}
    permissions = membership.effective_permissions
    can_edit = can_create_content(membership) and (
        post.author_id == membership.user_id or permissions.get("edit_others_posts", False)
    )
    moves = []
    if can_edit and statuses <= {"draft"}:
        moves.extend(stage for stage in ProductionStage.values if stage != pipeline_stage(post))
    if children and can_edit and statuses <= {"draft", "changes_requested", "rejected", "approved", "scheduled"}:
        if statuses <= {"draft", "changes_requested", "rejected"}:
            if review_available(post.workspace):
                # P1-B32: no review step in "No approvals" (the same rule refuses it in the service).
                moves.append(PipelineStage.REVIEW)
            if statuses & {"changes_requested", "rejected"}:
                moves.append(ProductionStage.EDITING)
        else:
            moves.append(ProductionStage.EDITING)
    if can_decide(post, membership, statuses):
        moves.append(PipelineStage.APPROVED)
    if (
        children
        and can_schedule(membership)
        and statuses <= {"draft", "approved"}
        and not any(release_blocker(post.workspace, status) for status in statuses)
    ):
        # P1-B27: Ready (planned) straight from a draft only where the approval mode allows it.
        moves.append(PipelineStage.READY)
    if any(is_planned(pp) for pp in children) and not can_unschedule(membership, post):
        # P1-B36: back to a production stage turns planned copies into drafts (their time dropped);
        # only a planner may take content out of the plan.
        moves = [stage for stage in moves if stage not in ProductionStage.values]
    return moves


# Channel copies that may still be planned or marked as published; the approval workflow decides
# which of them may go out now (``release_blocker``).
_UNPUBLISHED = {PlatformPost.Status.DRAFT, PlatformPost.Status.APPROVED, PlatformPost.Status.SCHEDULED}


def publication_blocker(child):
    """Why this channel copy may not be planned or marked as published now, or "" (P1-B27)."""
    reason = release_blocker(child.post.workspace, child.status)
    if reason:
        return reason
    if child.status not in _UNPUBLISHED:
        return "It was already published."
    return ""


@transaction.atomic
def request_content_changes(post, user, comment, *, expected_stage):
    """Send content in review back to the team with a required comment (F7-B02).

    Same authorization as approving (``can_decide``). The decision itself, its history record
    and the notification to the author come from Brightbean's ``request_changes``; the pipeline
    stage stays Review until the team resubmits.
    """
    post = Post.objects.select_for_update(of=("self",)).select_related("workspace").get(pk=post.pk)
    membership = content_membership(post.workspace, user)
    children = list(post.platform_posts.select_for_update(of=("self",)).order_by("pk"))
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
    # P1-B19: keep feedback in the shared discussion as well as the decision log.
    # The approval service already notifies its recipients: creating the comment
    # directly avoids a second client-comment or mention notification.
    PostComment.objects.create(post=post, author=user, body=comment, visibility=PostComment.Visibility.EXTERNAL)
    post.refresh_from_db()
    return post


@transaction.atomic
def move_content(post, stage, user, *, expected_stage, scheduled_at=None, comment=""):
    """Serialize changes, authorize the actor and delegate editorial decisions to Brightbean."""
    post = Post.objects.select_for_update(of=("self",)).select_related("workspace").get(pk=post.pk)
    membership = content_membership(post.workspace, user)
    # Copies by primary key after their content: the publisher's lock order (P1-B37).
    children = list(
        post.platform_posts.select_for_update(of=("self",)).select_related("social_account__jrc_channel").order_by("pk")
    )
    previous = pipeline_stage(post)
    if previous != expected_stage:
        raise ValidationError("This content changed. Refresh the board and try again.")
    if stage not in PipelineStage.values:
        raise ValidationError("Choose a valid stage.")
    if not children and stage not in ProductionStage.values:
        raise ValidationError("Add a channel before moving content to Review or later stages.")
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
        comment = (comment or "").strip()
        if len(comment) > 5000:
            raise ValidationError("Keep the approval comment under 5,000 characters.")
        approvals.approve_post(post, user, post.workspace, comment=comment)
    elif stage == PipelineStage.READY:
        if scheduled_at is None or timezone.is_naive(scheduled_at) or scheduled_at <= timezone.now():
            raise ValidationError("Choose a future publication date and time.")
        for child in children:
            # Manual JR channels wait in scheduled until a human marks publication.
            # The upstream scheduling service assumes live publishing (publishability
            # of media for automatic providers). Keep its state machine and aggregate
            # scheduling service; the approval workflow was checked by available_moves.
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
def set_publication_date(post, platform_post_id, user, scheduled_at):
    """Plan one manual copy using the board's scheduling permission and state rules."""
    post = Post.objects.select_for_update(of=("self",)).select_related("workspace").get(pk=post.pk)
    membership = content_membership(post.workspace, user)
    if not can_schedule(membership):
        raise PermissionDenied("You cannot schedule content.")
    child = (
        post.platform_posts.select_for_update(of=("self",))
        .select_related("social_account__jrc_channel")
        .filter(pk=platform_post_id)
        .first()
    )
    if child is None:
        raise ValidationError("Choose a channel of this content.")
    profile = getattr(child.social_account, "jrc_channel", None)
    if (
        profile is None
        or not profile.is_enabled
        or profile.kind not in {ChannelProfile.Kind.MANUAL, ChannelProfile.Kind.CUSTOM}
    ):
        raise ValidationError("Choose an enabled manual channel.")
    reason = publication_blocker(child)
    if reason:
        raise ValidationError(f"This channel cannot be planned yet. {reason}")
    if scheduled_at is None or timezone.is_naive(scheduled_at) or scheduled_at <= timezone.now():
        raise ValidationError("Choose a future publication date and time.")
    previous = pipeline_stage(post)
    if child.status != PlatformPost.Status.SCHEDULED:
        child.transition_to(PlatformPost.Status.SCHEDULED)
    child.scheduled_at = scheduled_at
    child.save(update_fields=[*PlatformPost.TRANSITION_FIELDS, "scheduled_at", "updated_at"])
    sync_post_scheduled_at(post)
    post.refresh_from_db()
    current = pipeline_stage(post)
    if current != previous:
        StageChange.objects.create(
            post=post, from_stage=previous, to_stage=current, user=user, **note_audience(post.workspace, user)
        )
    return child


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
    reason = publication_blocker(child)
    if reason:
        raise ValidationError(f"This channel cannot be marked as published yet. {reason}")
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


@transaction.atomic
def change_category(post, category_id, user):
    from apps.composer.models import ContentCategory

    from .models import ContentCategoryChange

    locked = Post.objects.select_for_update(of=("self",)).select_related("workspace", "category").get(pk=post.pk)
    membership = content_membership(locked.workspace, user)
    if not can_rename_content(locked, membership):
        raise PermissionDenied("You cannot edit this content.")
    category = None
    if category_id:
        try:
            category = ContentCategory.objects.get(pk=category_id, workspace=locked.workspace)
        except (ContentCategory.DoesNotExist, ValidationError) as exc:
            raise ValidationError("Choose a category from this workspace.") from exc
    if locked.category_id != (category.pk if category else None):
        ContentCategoryChange.objects.create(
            post=locked,
            old_name=locked.category.name if locked.category else "",
            new_name=category.name if category else "",
            user=user,
        )
        locked.category = category
        locked.save(update_fields=["category", "updated_at"])
    return category


def can_change_proposal(post):
    # A proposal must never be confused with the live publication schedule.
    return (
        post.scheduled_at is None
        and not post.platform_posts.filter(status__in=["scheduled", "publishing", "published"]).exists()
    )


@transaction.atomic
def change_proposed_date(post, proposed_at, user):
    from .models import ContentProposalChange

    locked = Post.objects.select_for_update(of=("self",)).select_related("workspace").get(pk=post.pk)
    membership = content_membership(locked.workspace, user)
    if not can_rename_content(locked, membership):
        raise PermissionDenied("You cannot edit this content.")
    if not can_change_proposal(locked):
        raise ValidationError("This content already has a publication schedule. Change it in Publish.")
    if proposed_at is not None and timezone.is_naive(proposed_at):
        raise ValidationError("Choose a date and time with a timezone.")
    if locked.proposed_publish_at != proposed_at:
        ContentProposalChange.objects.create(
            post=locked, old_at=locked.proposed_publish_at, new_at=proposed_at, user=user
        )
        locked.proposed_publish_at = proposed_at
        locked.save(update_fields=["proposed_publish_at", "updated_at"])
