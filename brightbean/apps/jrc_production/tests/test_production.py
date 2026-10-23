from unittest.mock import patch

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.composer.models import PlatformPost, Post
from apps.jrc_channels.services import create_manual_channel, set_channel_enabled
from apps.jrc_production.models import ManualPublication, PostProduction, ProductionStage, StageChange
from apps.jrc_production.services import create_content, pipeline_stage
from apps.social_accounts.models import SocialAccount
from apps.workspaces.models import Workspace

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("stage", ProductionStage.values)
def test_draft_uses_only_stored_production_stage(workspace, user, stage):
    create_manual_channel(workspace, "instagram", "Test IG")
    post = create_content(workspace, "Test reel", user)
    PostProduction.objects.filter(post=post).update(stage=stage)

    fresh = Post.objects.get(pk=post.pk)
    assert pipeline_stage(fresh) == stage
    assert fresh.status == "draft"


@pytest.mark.parametrize(
    ("statuses", "expected"),
    [
        (["pending_review"], "review"),
        (["pending_client"], "review"),
        (["changes_requested"], "review"),
        (["rejected"], "review"),
        (["on_hold"], "review"),
        (["approved"], "approved"),
        (["scheduled"], "ready"),
        (["publishing"], "ready"),
        (["failed"], "ready"),
        (["published"], "published"),
        (["published", "failed"], "published"),
        (["published", "on_hold"], "published"),
        (["draft", "approved"], "editing"),
        (["pending_client", "approved"], "review"),
        (["changes_requested", "scheduled"], "review"),
        (["approved", "scheduled"], "approved"),
        (["scheduled", "published"], "ready"),
        (["failed", "scheduled"], "ready"),
        (["published", "published"], "published"),
    ],
)
def test_pipeline_uses_upstream_aggregate(workspace, user, statuses, expected):
    for index in range(len(statuses)):
        create_manual_channel(workspace, "instagram", f"Test IG {index}")
    post = create_content(workspace, "Test reel", user)
    PostProduction.objects.filter(post=post).update(stage="editing")
    for pp, status in zip(post.platform_posts.order_by("id"), statuses, strict=True):
        PlatformPost.objects.filter(pk=pp.pk).update(status=status)

    fresh = Post.objects.prefetch_related("platform_posts").get(pk=post.pk)
    assert pipeline_stage(fresh) == expected
    assert fresh.jrc_production.stage == "editing"


def test_legacy_draft_falls_back_to_idea_without_writing(workspace):
    post = Post.objects.create(workspace=workspace)
    assert pipeline_stage(post) == "idea"
    assert not PostProduction.objects.exists()


def test_legacy_review_needs_no_extension(workspace):
    post = Post.objects.create(workspace=workspace)
    channel = create_manual_channel(workspace, "instagram", "Test IG")
    PlatformPost.objects.create(post=post, social_account=channel, status="pending_review")
    assert pipeline_stage(post) == "review"
    assert not PostProduction.objects.exists()


def test_return_to_draft_reveals_production_stage(workspace, user):
    create_manual_channel(workspace, "instagram", "Test IG")
    post = create_content(workspace, "Test reel", user)
    pp = post.platform_posts.get()
    for status in ("pending_review", "approved"):
        pp.transition_to(status)
        pp.save(update_fields=PlatformPost.TRANSITION_FIELDS)
    assert pipeline_stage(post) == "approved"

    pp.transition_to("draft")
    pp.save(update_fields=PlatformPost.TRANSITION_FIELDS)
    production = post.jrc_production
    production.stage = "editing"
    production.save(update_fields=["stage"])
    assert pipeline_stage(post) == "editing"


def test_create_content_includes_exactly_enabled_workspace_channels(workspace, organization, user):
    instagram = create_manual_channel(workspace, "instagram", "Test IG")
    custom = create_manual_channel(workspace, "custom", "Test Forendors")
    disabled = create_manual_channel(workspace, "youtube", "Disabled")
    set_channel_enabled(disabled, enabled=False)
    other = Workspace.objects.create(organization=organization, name="Other client")
    create_manual_channel(other, "instagram", "Other IG")
    # A connected upstream account without a local profile is not an enabled JR channel.
    upstream = SocialAccount(workspace=workspace, platform="instagram", account_platform_id="test-upstream")
    upstream._skip_analytics_backfill = True
    upstream.save()

    before = timezone.now()
    post = create_content(workspace, "Test reel", user)
    production = post.jrc_production
    assert post.title == "Test reel"
    assert post.workspace == workspace
    assert post.author == user
    assert production.stage == "idea"
    assert production.stage_changed_by == user
    assert before <= production.stage_changed_at <= timezone.now()
    assert set(post.platform_posts.values_list("social_account_id", flat=True)) == {instagram.pk, custom.pk}
    assert set(post.platform_posts.values_list("status", flat=True)) == {"draft"}
    assert pipeline_stage(post) == "idea"
    assert Post.objects.for_workspace(workspace.pk).get() == post


@pytest.mark.parametrize("disabled", [False, True])
def test_no_enabled_channels_rejects_invisible_content(workspace, user, disabled):
    if disabled:
        channel = create_manual_channel(workspace, "instagram", "Disabled IG")
        set_channel_enabled(channel, enabled=False)
    with pytest.raises(ValidationError, match="Enable at least one channel"):
        create_content(workspace, "Invisible reel", user)
    assert not Post.objects.exists()
    assert not PostProduction.objects.exists()
    assert not PlatformPost.objects.exists()


def test_failure_rolls_back_entire_content(workspace, user):
    create_manual_channel(workspace, "instagram", "Test IG")
    with (
        patch.object(PlatformPost.objects, "bulk_create", side_effect=IntegrityError("test failure")),
        pytest.raises(IntegrityError, match="test failure"),
    ):
        create_content(workspace, "Test reel", user)
    assert not Post.objects.exists()
    assert not PostProduction.objects.exists()
    assert not PlatformPost.objects.exists()


def test_invalid_title_leaves_no_content(workspace, user):
    create_manual_channel(workspace, "instagram", "Test IG")
    with pytest.raises(ValidationError):
        create_content(workspace, "x" * 256, user)
    assert not Post.objects.exists()


@pytest.mark.parametrize("invalid_stage", ["review", "approved", "ready", "published", "unknown"])
def test_database_rejects_nonproduction_stage(workspace, invalid_stage):
    post = Post.objects.create(workspace=workspace)
    with pytest.raises(IntegrityError), transaction.atomic():
        PostProduction.objects.create(post=post, stage=invalid_stage)


def test_history_and_publication_survive_user_deletion_and_follow_content_deletion(workspace, user):
    create_manual_channel(workspace, "instagram", "Test IG")
    post = create_content(workspace, "Test reel", user)
    change = StageChange.objects.create(
        post=post, from_stage="approved", to_stage="editing", user=user, note="New cut requested"
    )
    published_at = timezone.now()
    publication = ManualPublication.objects.create(
        platform_post=post.platform_posts.get(),
        published_by=user,
        published_at=published_at,
        post_url="https://example.invalid/posts/test-reel",
    )
    assert post.jrc_stage_changes.get() == change
    assert post.platform_posts.get().jrc_publication == publication
    assert publication.published_at == published_at
    with pytest.raises(IntegrityError), transaction.atomic():
        ManualPublication.objects.create(platform_post=publication.platform_post, published_at=published_at)

    user.delete()
    change.refresh_from_db()
    publication.refresh_from_db()
    production = PostProduction.objects.get(post=post)
    assert production.stage_changed_by is None
    assert change.user is None
    assert change.note == "New cut requested"
    assert publication.published_by is None
    assert publication.post_url == "https://example.invalid/posts/test-reel"
    post.delete()
    assert not StageChange.objects.exists()
    assert not ManualPublication.objects.exists()
    assert not PostProduction.objects.exists()
