"""P1-B06: unassigned content, explicit channels and safe channel removal."""

import uuid

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import RequestFactory
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.approvals.models import PostComment
from apps.calendar.views import _get_filtered_platform_posts, _get_filtered_posts, _get_tab_context, _publish_tab_counts
from apps.composer.models import PlatformPost, Post
from apps.jrc_channels.services import create_manual_channel, set_channel_enabled
from apps.jrc_production.models import ProductionStage, StageChange
from apps.jrc_production.services import (
    add_content_channel,
    available_moves,
    create_content,
    move_content,
    pipeline_stage,
    remove_content_channel,
)
from apps.jrc_texts import services as texts
from apps.jrc_video.models import VideoAsset
from apps.members.models import WorkspaceMembership

pytestmark = pytest.mark.django_db


def detail(post):
    return reverse("jrc_production:content_detail", args=[post.workspace_id, post.pk])


def test_new_client_channel_never_attaches_old_content(client, workspace, user):
    first = create_manual_channel(workspace, "instagram", "IG")
    post = create_content(workspace, "Keep this idea unassigned", user)
    create_manual_channel(workspace, "youtube", "YT added later")
    client.force_login(user)
    for tab in ("script", "video", "caption", "publish", "history", "comments"):
        response = client.get(detail(post), {"tab": tab})
        assert response.status_code == 200
        if tab in {"video", "caption", "publish"}:
            assert b"Add a channel first" in response.content
            assert b"data-open-channels" in response.content
    assert not post.platform_posts.exists()
    assert (
        client.post(
            detail(post), {"action": "save_caption", "platform_post": f"new:{first.pk}", "caption": "x"}
        ).status_code
        == 404
    )
    assert not post.platform_posts.exists()


@pytest.mark.parametrize("stage", ProductionStage.values)
def test_all_production_stages_without_channel(client, workspace, user, stage):
    post = create_content(workspace, f"Unassigned {stage}", user)
    if stage != "idea":
        post = move_content(post, stage, user, expected_stage="idea")
        assert StageChange.objects.get(post=post).to_stage == stage
    membership = WorkspaceMembership.objects.get(workspace=workspace, user=user)
    assert pipeline_stage(post) == stage
    assert set(available_moves(post, membership)) == set(ProductionStage.values) - {stage}
    client.force_login(user)
    board = client.get(reverse("jrc_production:board"), {"channel": "none"})
    assert board.status_code == 200 and board.context["total"] == 1
    assert post.title.encode() in board.content
    for destination in ("review", "approved", "ready", "published"):
        with pytest.raises(ValidationError, match="Add a channel"):
            move_content(post, destination, user, expected_stage=stage)
    assert not post.platform_posts.exists()


def test_explicit_channel_selection_and_http_creation(client, workspace, user):
    first = create_manual_channel(workspace, "instagram", "IG")
    second = create_manual_channel(workspace, "youtube", "YT")
    disabled = create_manual_channel(workspace, "custom", "Off")
    set_channel_enabled(disabled, enabled=False)
    client.force_login(user)
    url = reverse("jrc_production:create")
    assert client.post(url, {"workspace": workspace.pk, "title": "No selection"}).status_code == 302
    assert not Post.objects.get(title="No selection").platform_posts.exists()
    assert (
        client.post(url, {"workspace": workspace.pk, "title": "One selection", "channels": [first.pk]}).status_code
        == 302
    )
    assert list(Post.objects.get(title="One selection").platform_posts.values_list("social_account_id", flat=True)) == [
        first.pk
    ]
    assert (
        client.post(url, {"workspace": workspace.pk, "title": "Bad", "channels": [second.pk, disabled.pk]}).status_code
        == 400
    )
    assert not Post.objects.filter(title="Bad").exists()


def test_add_remove_channels_preserves_stage_and_script(client, workspace, user):
    account = create_manual_channel(workspace, "instagram", "IG")
    post = create_content(workspace, "An idea", user)
    post = move_content(post, "editing", user, expected_stage="idea")
    script = texts.create_document(post.pk, user, markdown="# Keep the script")
    variant = add_content_channel(post, account.pk, user)
    assert add_content_channel(post, account.pk, user).pk == variant.pk
    assert post.platform_posts.count() == 1 and pipeline_stage(post) == "editing"
    remove_content_channel(post, variant.pk, user)
    assert not post.platform_posts.exists() and post.jrc_texts.get().pk == script.pk
    assert pipeline_stage(post) == "editing"
    client.force_login(user)
    assert client.post(detail(post), {"action": "add_channel", "channel": account.pk}).status_code == 302
    variant = post.platform_posts.get()
    assert client.post(detail(post), {"action": "remove_channel", "platform_post": variant.pk}).status_code == 302
    assert not post.platform_posts.exists()


@pytest.mark.parametrize("history", ["caption", "video"])
def test_cannot_remove_channel_with_saved_work(workspace, user, history):
    account = create_manual_channel(workspace, "instagram", "IG")
    post = create_content(workspace, "Keep saved work", user)
    variant = add_content_channel(post, account.pk, user)
    if history == "caption":
        texts.save_caption(post.pk, variant.pk, user, "Saved caption")
    else:
        VideoAsset.objects.create(platform_post=variant, ff_asset_id=uuid.uuid4())
    with pytest.raises(ValidationError, match="caption history or video"):
        remove_content_channel(post, variant.pk, user)
    assert PlatformPost.objects.filter(pk=variant.pk).exists()


@pytest.mark.parametrize(
    "role,own,allowed",
    [
        ("owner", False, True),
        ("manager", False, True),
        ("editor", False, True),
        ("contributor", True, True),
        ("contributor", False, False),
        ("client", True, False),
        ("viewer", True, False),
    ],
)
def test_channel_management_permissions(workspace, user, role, own, allowed):
    account = create_manual_channel(workspace, "instagram", "IG")
    post = create_content(workspace, "Role test", user)
    actor = User.objects.create_user(email="role@example.test")
    WorkspaceMembership.objects.create(workspace=workspace, user=actor, workspace_role=role)
    if own:
        post.author = actor
        post.save(update_fields=["author"])
    if allowed:
        variant = add_content_channel(post, account.pk, actor)
        remove_content_channel(post, variant.pk, actor)
    else:
        with pytest.raises(PermissionDenied):
            add_content_channel(post, account.pk, actor)
    assert not post.platform_posts.exists()


def test_review_requires_channel_and_locks_channel_changes(workspace, user):
    account = create_manual_channel(workspace, "instagram", "IG")
    other = create_manual_channel(workspace, "custom", "Web")
    post = create_content(workspace, "Review", user)
    variant = add_content_channel(post, account.pk, user)
    post = move_content(post, "review", user, expected_stage="idea")
    for operation in (
        lambda: remove_content_channel(post, variant.pk, user),
        lambda: add_content_channel(post, other.pk, user),
    ):
        with pytest.raises(ValidationError, match="back to Editing"):
            operation()
    assert post.platform_posts.count() == 1


def test_unassigned_list_filters_counts_and_no_calendar_date(client, workspace, user):
    post = create_content(workspace, "Unscheduled unassigned content", user)
    account = create_manual_channel(workspace, "instagram", "IG")
    assigned = create_content(workspace, "Assigned", user, social_account_ids=[account.pk])
    factory = RequestFactory()
    request = factory.get("/", {"channel": "none"})
    request.user = user
    assert list(_get_filtered_posts(workspace, request)) == [post]
    assert not _get_filtered_platform_posts(workspace, request).exists()
    assert _publish_tab_counts(workspace, request)["drafts_count"] == 1
    assert list(_get_tab_context(request, workspace, "drafts")["channel_less_posts"]) == [post]
    client.force_login(user)
    url = reverse("calendar:calendar", args=[workspace.pk])
    page = client.get(url, {"mode": "list", "tab": "drafts", "channel": "none"})
    assert page.status_code == 200 and post.title.encode() in page.content
    assert assigned.title.encode() not in page.content
    calendar = client.get(url, {"mode": "calendar", "view": "month"})
    assert calendar.status_code == 200 and post.title.encode() not in calendar.content


def test_unassigned_comments_and_visibility(client, workspace, user):
    post = create_content(workspace, "Shared idea", user)
    client.force_login(user)
    response = client.post(
        reverse("jrc_production:add_content_comment", args=[workspace.pk, post.pk]), {"body": "Internal note"}
    )
    assert response.status_code == 302
    assert PostComment.objects.get(post=post).visibility == "internal"
    client_member = User.objects.create_user(email="client@example.test", tos_accepted_at=timezone.now())
    WorkspaceMembership.objects.create(workspace=workspace, user=client_member, workspace_role="client")
    client.force_login(client_member)
    response = client.get(detail(post), {"tab": "comments"})
    assert response.status_code == 200 and b"Internal note" not in response.content
    WorkspaceMembership.objects.filter(user=client_member, workspace=workspace).delete()
    assert client.get(detail(post)).status_code == 404


@pytest.mark.parametrize("view", ["month", "week", "day"])
def test_dated_unassigned_content_in_calendar_has_no_publication_slot(client, workspace, user, view):
    post = create_content(workspace, "Dated unassigned content", user)
    post.proposed_publish_at = timezone.now()
    post.save(update_fields=["proposed_publish_at"])
    client.force_login(user)
    page = client.get(
        reverse("calendar:calendar", args=[workspace.pk]),
        {"mode": "calendar", "view": view, "date": post.proposed_publish_at.date().isoformat(), "channel": "none"},
    )
    assert page.status_code == 200
    assert post.title.encode() in page.content and b"No channel" in page.content
    assert b'draggable="true"' not in page.content
    assert not post.platform_posts.exists()


def test_video_upload_does_not_implicitly_attach_channel(workspace, user):
    from apps.jrc_video.uploads import UploadError, _resolve_platform_post

    post = create_content(workspace, "Unassigned video idea", user)
    channel = create_manual_channel(workspace, "instagram", "Added later")
    with pytest.raises(UploadError, match="Add this channel"):
        _resolve_platform_post(post, f"new:{channel.pk}")
    assert not post.platform_posts.exists()
