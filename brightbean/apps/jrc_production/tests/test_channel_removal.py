"""P1-B06 review1: every UI removal path protects work and keeps channel-less content."""

import uuid

import pytest
from django.urls import reverse

from apps.approvals.models import PostComment
from apps.composer.models import PlatformPost, Post
from apps.jrc_channels.services import create_manual_channel
from apps.jrc_production.models import StageChange
from apps.jrc_production.services import add_content_channel, create_content, move_content
from apps.jrc_texts import services as texts
from apps.jrc_thumbnails.models import ChannelThumbnail
from apps.jrc_video.models import VideoAsset, VideoVersion

from .test_composer_captions import _composer_autosave, _composer_save

pytestmark = pytest.mark.django_db
ENDPOINTS = ["save", "autosave", "autosave_fallback", "composer_delete", "calendar_delete", "detail"]


def remove_via(client, post, variant, endpoint):
    if endpoint == "save":
        return _composer_save(client, post, [], title="Stale title")
    if endpoint.startswith("autosave"):
        return _composer_autosave(client, post, [], fallback=endpoint == "autosave_fallback")
    if endpoint == "composer_delete":
        return client.post(
            reverse("composer:post_delete", args=[post.workspace_id, post.pk]) + f"?account={variant.social_account_id}"
        )
    if endpoint == "calendar_delete":
        return client.post(
            reverse("calendar:bulk_platform_action", args=[post.workspace_id]),
            {"action": "delete", "platform_post_ids": list(post.platform_posts.values_list("pk", flat=True))},
        )
    return client.post(
        reverse("jrc_production:content_detail", args=[post.workspace_id, post.pk]),
        {"action": "remove_channel", "platform_post": variant.pk},
        follow=True,
    )


@pytest.mark.parametrize("endpoint", ENDPOINTS)
@pytest.mark.parametrize("history", ["caption", "video", "thumbnail"])
def test_all_channel_removal_paths_protect_saved_work(client, workspace, user, endpoint, history):
    account = create_manual_channel(workspace, "instagram", "IG")
    post = create_content(workspace, "Keep this work", user, social_account_ids=[account.pk])
    variant = post.platform_posts.get()
    other = create_manual_channel(workspace, "custom", "Empty sibling")
    sibling = add_content_channel(post, other.pk, user)
    if history == "caption":
        texts.save_caption(post.pk, variant.pk, user, "Keep every caption version")
        saved = list(variant.jrc_texts.get().versions.values())
    elif history == "video":
        video = VideoAsset.objects.create(platform_post=variant, ff_asset_id=uuid.uuid4())
        VideoVersion.objects.create(video=video, number=1, ff_version_id=uuid.uuid4(), uploaded_by=user)
        saved = list(video.versions.values())
    else:
        ChannelThumbnail.objects.create(
            post=post,
            platform_post=variant,
            number=1,
            image="jrc_thumbnails/keep.png",
            content_type="image/png",
            size=10,
            width=2,
            height=2,
            uploaded_by=user,
        )
        saved = list(variant.jrc_thumbnails.values())
    client.force_login(user)

    response = remove_via(client, post, variant, endpoint)

    if endpoint in {"save", "composer_delete", "calendar_delete"}:
        assert response.status_code == 409, response.content
    else:
        assert response.status_code == 200, response.content
    assert (
        b"caption history, video or thumbnails" in response.content
        or b"captionSelectionConflict" in response.headers.get("HX-Trigger", "").encode()
    )
    post.refresh_from_db()
    assert post.title == "Keep this work"
    assert PlatformPost.objects.filter(pk=variant.pk).exists()
    assert PlatformPost.objects.filter(pk=sibling.pk).exists()  # refuse the entire save/batch
    if history == "caption":
        assert list(variant.jrc_texts.get().versions.values()) == saved
    elif history == "video":
        assert VideoAsset.objects.filter(pk=video.pk).exists()
        assert list(video.versions.values()) == saved
    else:
        assert list(variant.jrc_thumbnails.values()) == saved


@pytest.mark.parametrize("endpoint", ENDPOINTS)
def test_removing_last_empty_channel_preserves_content_and_its_history(client, workspace, user, endpoint):
    account = create_manual_channel(workspace, "instagram", "IG")
    post = create_content(workspace, "Keep the idea", user, social_account_ids=[account.pk])
    variant = post.platform_posts.get()
    move_content(post, "editing", user, expected_stage="idea")
    script = texts.create_document(post.pk, user, markdown="# Keep this script")
    versions = list(script.versions.values())
    comment = PostComment.objects.create(post=post, author=user, body="Keep this discussion")
    changes = list(StageChange.objects.filter(post=post).values())
    client.force_login(user)

    response = remove_via(client, post, variant, endpoint)

    assert response.status_code in {200, 204, 302}, response.content
    post.refresh_from_db()
    assert not post.platform_posts.exists()
    assert post.jrc_production.stage == "editing"
    assert PostComment.objects.filter(pk=comment.pk, post=post).exists()
    assert list(script.versions.values()) == versions
    assert list(StageChange.objects.filter(post=post).values()) == changes


def test_explicit_whole_content_delete_remains_separate(client, workspace, user):
    account = create_manual_channel(workspace, "instagram", "IG")
    post = create_content(workspace, "Explicit deletion", user, social_account_ids=[account.pk])
    variant = post.platform_posts.get()
    texts.save_caption(post.pk, variant.pk, user, "Caption history")
    video = VideoAsset.objects.create(platform_post=variant, ff_asset_id=uuid.uuid4())
    client.force_login(user)
    # No account means the explicit whole-content action, including its history.
    response = client.post(reverse("composer:post_delete", args=[workspace.pk, post.pk]))
    assert response.status_code == 204
    assert not Post.objects.filter(pk=post.pk).exists()
    assert not VideoAsset.objects.filter(pk=video.pk).exists()
