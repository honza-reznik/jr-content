from datetime import timedelta
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import pytest
from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from apps.composer.models import PlatformPost
from apps.composer.services import transition_platform_post
from apps.jrc_channels.services import create_manual_channel
from apps.jrc_production.models import ManualPublication
from apps.jrc_production.services import create_content, mark_published_manually
from apps.jrc_video.models import VideoAsset, VideoVersion
from apps.members.models import CustomRole, WorkspaceMembership
from apps.workspaces.models import Workspace

pytestmark = pytest.mark.django_db


@pytest.fixture
def post(workspace, user):
    create_manual_channel(workspace, "instagram", "Publish test channel")
    return create_content(workspace, "Publish test content", user)


def approved_post(post):
    variant = post.platform_posts.get()
    variant.platform_specific_caption = "A saved caption"
    variant.save(update_fields=["platform_specific_caption", "updated_at"])
    transition_platform_post(variant, PlatformPost.Status.PENDING_REVIEW)
    variant.refresh_from_db()
    transition_platform_post(variant, PlatformPost.Status.APPROVED)
    return variant


def test_mark_published_manually_records_channel_actor_time_url_and_history(client, workspace, user, post):
    variant = approved_post(post)
    publication = mark_published_manually(post, variant.pk, user, post_url="https://social.example/post/123")

    variant.refresh_from_db()
    assert variant.status == PlatformPost.Status.PUBLISHED
    assert publication.platform_post == variant
    assert publication.published_by == user
    assert publication.post_url == "https://social.example/post/123"
    assert publication.published_at <= timezone.now()
    assert ManualPublication.objects.count() == 1

    client.force_login(user)
    detail = reverse("jrc_production:content_detail", kwargs={"workspace_id": workspace.pk, "post_id": post.pk})
    assert b"Published manually" in client.get(detail, {"tab": "publish"}).content
    assert b"social.example/post/123" in client.get(detail, {"tab": "history"}).content


def test_viewer_cannot_mark_published(client, workspace, user, post):
    variant = approved_post(post)
    WorkspaceMembership.objects.filter(workspace=workspace, user=user).update(workspace_role="viewer")
    with pytest.raises(PermissionDenied):
        mark_published_manually(post, variant.pk, user)

    client.force_login(user)
    url = reverse("jrc_production:mark_published", kwargs={"workspace_id": workspace.pk, "post_id": post.pk})
    assert client.post(url, {"platform_post": variant.pk}).status_code == 403
    assert ManualPublication.objects.count() == 0


def test_client_can_mark_approved_content_published(client, workspace, user, post):
    variant = approved_post(post)
    WorkspaceMembership.objects.filter(workspace=workspace, user=user).update(workspace_role="client")
    client.force_login(user)
    url = reverse("jrc_production:mark_published", kwargs={"workspace_id": workspace.pk, "post_id": post.pk})
    assert client.post(url, {"platform_post": variant.pk}).status_code == 302
    assert ManualPublication.objects.filter(platform_post=variant, published_by=user).count() == 1


def test_client_publish_requires_ready_final_video(client, workspace, user, post):
    variant = approved_post(post)
    video = VideoAsset.objects.create(platform_post=variant, ff_asset_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
    WorkspaceMembership.objects.filter(workspace=workspace, user=user).update(workspace_role="client")
    from django.core.exceptions import ValidationError

    with pytest.raises(ValidationError, match="converted video"):
        mark_published_manually(post, variant.pk, user)

    version = VideoVersion.objects.create(
        video=video,
        number=1,
        ff_version_id="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        status=VideoVersion.Status.READY,
    )
    video.final_version = version
    video.save(update_fields=["final_version"])
    assert mark_published_manually(post, variant.pk, user)


def test_client_can_publish_even_with_custom_role_without_publish_grant(client, workspace, user, post):
    variant = approved_post(post)
    custom = CustomRole.objects.create(
        organization=workspace.organization,
        name="Client role custom permissions",
        permissions={},
    )
    WorkspaceMembership.objects.filter(workspace=workspace, user=user).update(
        workspace_role="client", custom_role=custom
    )
    assert mark_published_manually(post, variant.pk, user)


def test_mark_published_rejects_foreign_channel_and_non_https_url(client, workspace, organization, user, post):
    variant = approved_post(post)
    foreign = Workspace.objects.create(organization=organization, name="Other client")
    WorkspaceMembership.objects.create(workspace=foreign, user=user, workspace_role="owner")
    foreign_channel = create_manual_channel(foreign, "instagram", "Other channel")
    from apps.composer.models import Post

    foreign_post = Post.objects.create(workspace=foreign, title="Foreign")
    foreign_variant = PlatformPost.objects.create(post=foreign_post, social_account=foreign_channel)
    with pytest.raises(ValidationError):
        mark_published_manually(post, foreign_variant.pk, user)
    with pytest.raises(ValidationError):
        mark_published_manually(post, variant.pk, user, post_url="http://social.example/post/123")
    assert ManualPublication.objects.count() == 0


def test_mark_published_requires_caption_and_final_video_when_video_exists(user, post):
    variant = post.platform_posts.get()
    transition_platform_post(variant, PlatformPost.Status.PENDING_REVIEW)
    variant.refresh_from_db()
    transition_platform_post(variant, PlatformPost.Status.APPROVED)
    variant.refresh_from_db()
    variant.platform_specific_caption = ""
    variant.save(update_fields=["platform_specific_caption", "updated_at"])
    with pytest.raises(ValidationError, match="caption"):
        mark_published_manually(post, variant.pk, user)

    variant.platform_specific_caption = "Caption"
    variant.save(update_fields=["platform_specific_caption", "updated_at"])
    video = VideoAsset.objects.create(platform_post=variant, ff_asset_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
    with pytest.raises(ValidationError, match="final"):
        mark_published_manually(post, variant.pk, user)

    version = VideoVersion.objects.create(
        video=video,
        number=1,
        ff_version_id="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        status=VideoVersion.Status.READY,
    )
    video.final_version = version
    video.save(update_fields=["final_version"])
    assert mark_published_manually(post, variant.pk, user)


def test_final_download_is_scoped_and_redirects_only_to_short_signed_storage_url(client, workspace, user, post):
    variant = post.platform_posts.get()
    video = VideoAsset.objects.create(platform_post=variant, ff_asset_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
    version = VideoVersion.objects.create(
        video=video,
        number=1,
        ff_version_id="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        status=VideoVersion.Status.READY,
    )
    video.final_version = version
    video.save(update_fields=["final_version"])
    client.force_login(user)
    url = reverse(
        "jrc_video:final_download",
        kwargs={"workspace_id": workspace.pk, "post_id": post.pk, "platform_post_id": variant.pk},
    )
    signed = "http://127.0.0.1:14133/jrc-video/file.mp4?X-Amz-Expires=600&X-Amz-Signature=signature"
    with (
        override_settings(JRC_S3_PUBLIC_ORIGIN="http://127.0.0.1:14133"),
        patch("apps.jrc_video.uploads._call", return_value={"url": signed}) as call,
    ):
        response = client.get(url)
    assert response.status_code == 302
    assert response["Location"] == signed
    assert "no-store" in response["Cache-Control"]
    assert "token=" not in response["Location"]
    assert "signature" not in call.call_args.args[2]
    query = parse_qs(urlsplit(call.call_args.args[2]).query)
    assert query["filename"] == ["publish-test-content-instagram-publish-test-channel-v1.mp4"]


def test_final_download_filename_is_ascii_and_safe(client, workspace, user, post):
    post.title = 'Žluťoučký / teaser "one"'
    post.save(update_fields=["title"])
    variant = post.platform_posts.get()
    variant.social_account.account_name = 'Kanál / "A"'
    variant.social_account.save(update_fields=["account_name"])
    video = VideoAsset.objects.create(platform_post=variant, ff_asset_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
    version = VideoVersion.objects.create(
        video=video,
        number=3,
        ff_version_id="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        status=VideoVersion.Status.READY,
    )
    video.final_version = version
    video.save(update_fields=["final_version"])
    client.force_login(user)
    url = reverse(
        "jrc_video:final_download",
        kwargs={"workspace_id": workspace.pk, "post_id": post.pk, "platform_post_id": variant.pk},
    )
    signed = "http://127.0.0.1:14133/file.mp4?X-Amz-Expires=600&X-Amz-Signature=signed-disposition"
    with (
        override_settings(JRC_S3_PUBLIC_ORIGIN="http://127.0.0.1:14133"),
        patch("apps.jrc_video.uploads._call", return_value={"url": signed}) as call,
    ):
        response = client.get(url)
    assert response.status_code == 302
    query = parse_qs(urlsplit(call.call_args.args[2]).query)
    filename = query["filename"][0]
    assert filename == "zlutoucky-teaser-one-instagram-kanal-a-v3.mp4"
    assert filename.isascii()
    assert not any(char in filename for char in '/\\"')


def test_content_detail_renders_flash_message_once(client, workspace, user, post):
    client.force_login(user)
    url = reverse("jrc_production:add_content_comment", kwargs={"workspace_id": workspace.pk, "post_id": post.pk})
    response = client.post(url, {"body": "A comment", "visibility": "external"}, follow=True)
    assert response.status_code == 200
    assert response.content.count(b"Comment added.") == 1


@pytest.mark.parametrize("role", ["viewer", "editor"])
def test_final_download_requires_publish_permission_and_hides_link(client, workspace, user, post, role):
    variant = post.platform_posts.get()
    video = VideoAsset.objects.create(platform_post=variant, ff_asset_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
    version = VideoVersion.objects.create(
        video=video,
        number=1,
        ff_version_id="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        status=VideoVersion.Status.READY,
    )
    video.final_version = version
    video.save(update_fields=["final_version"])
    WorkspaceMembership.objects.filter(workspace=workspace, user=user).update(workspace_role=role)
    client.force_login(user)
    url = reverse(
        "jrc_video:final_download",
        kwargs={"workspace_id": workspace.pk, "post_id": post.pk, "platform_post_id": variant.pk},
    )
    with patch("apps.jrc_video.uploads._call") as call:
        assert client.get(url).status_code == 403
        call.assert_not_called()

    detail = reverse("jrc_production:content_detail", kwargs={"workspace_id": workspace.pk, "post_id": post.pk})
    response = client.get(detail, {"tab": "publish"})
    assert response.status_code == 200
    assert url.encode() not in response.content


def test_client_can_download_even_with_custom_role(client, workspace, user, post):
    variant = post.platform_posts.get()
    video = VideoAsset.objects.create(platform_post=variant, ff_asset_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
    version = VideoVersion.objects.create(
        video=video,
        number=1,
        ff_version_id="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        status=VideoVersion.Status.READY,
    )
    video.final_version = version
    video.save(update_fields=["final_version"])
    custom = CustomRole.objects.create(
        organization=workspace.organization,
        name="Client without publish permission grant",
        permissions={},
    )
    WorkspaceMembership.objects.filter(workspace=workspace, user=user).update(
        workspace_role="client", custom_role=custom
    )
    client.force_login(user)
    url = reverse(
        "jrc_video:final_download",
        kwargs={"workspace_id": workspace.pk, "post_id": post.pk, "platform_post_id": variant.pk},
    )
    with (
        override_settings(JRC_S3_PUBLIC_ORIGIN="http://127.0.0.1:13453"),
        patch(
            "apps.jrc_video.uploads._call",
            return_value={
                "url": f"{settings.JRC_S3_PUBLIC_ORIGIN}/storage/jrc-video/final.mp4?X-Amz-Expires=900&X-Amz-Signature=local-test"
            },
        ) as call,
    ):
        response = client.get(url)
        assert response.status_code == 302
        call.assert_called_once()

    detail = reverse("jrc_production:content_detail", kwargs={"workspace_id": workspace.pk, "post_id": post.pk})
    response = client.get(detail, {"tab": "publish"})
    assert response.status_code == 200
    assert url.encode() in response.content


def test_final_download_rejects_foreign_channel_and_freeframe_token_url(client, workspace, organization, user, post):
    from apps.composer.models import Post

    own_variant = post.platform_posts.get()
    foreign_workspace = Workspace.objects.create(organization=organization, name="Foreign download client")
    foreign_channel = create_manual_channel(foreign_workspace, "instagram", "Foreign channel")
    foreign_post = Post.objects.create(workspace=foreign_workspace, title="Foreign")
    foreign_variant = PlatformPost.objects.create(post=foreign_post, social_account=foreign_channel)
    foreign_video = VideoAsset.objects.create(
        platform_post=foreign_variant, ff_asset_id="cccccccc-cccc-4ccc-8ccc-cccccccccccc"
    )
    foreign_version = VideoVersion.objects.create(
        video=foreign_video,
        number=1,
        ff_version_id="dddddddd-dddd-4ddd-8ddd-dddddddddddd",
        status=VideoVersion.Status.READY,
    )
    foreign_video.final_version = foreign_version
    foreign_video.save(update_fields=["final_version"])
    client.force_login(user)
    url = reverse(
        "jrc_video:final_download",
        kwargs={"workspace_id": workspace.pk, "post_id": post.pk, "platform_post_id": foreign_variant.pk},
    )
    with patch("apps.jrc_video.uploads._call") as call:
        assert client.get(url).status_code == 404
        call.assert_not_called()

    video = VideoAsset.objects.create(platform_post=own_variant, ff_asset_id="eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee")
    version = VideoVersion.objects.create(
        video=video,
        number=1,
        ff_version_id="ffffffff-ffff-4fff-8fff-ffffffffffff",
        status=VideoVersion.Status.READY,
    )
    video.final_version = version
    video.save(update_fields=["final_version"])
    signed_with_token = "http://127.0.0.1:14133/file.mp4?X-Amz-Expires=600&token=secret"
    with (
        override_settings(JRC_S3_PUBLIC_ORIGIN="http://127.0.0.1:14133"),
        patch("apps.jrc_video.uploads._call", return_value={"url": signed_with_token}),
    ):
        response = client.get(
            reverse(
                "jrc_video:final_download",
                kwargs={"workspace_id": workspace.pk, "post_id": post.pk, "platform_post_id": own_variant.pk},
            )
        )
    assert response.status_code == 502
    assert b"secret" not in response.content


def test_ready_or_scheduled_time_is_rendered_using_workspace_timezone(client, workspace, user, post):
    workspace.timezone = "Europe/Prague"
    workspace.save(update_fields=["timezone"])
    variant = approved_post(post)
    scheduled = timezone.now() + timedelta(days=2)
    variant.status = PlatformPost.Status.SCHEDULED
    variant.scheduled_at = scheduled
    variant.save(update_fields=["status", "scheduled_at", "updated_at"])
    client.force_login(user)
    url = reverse("jrc_production:content_detail", kwargs={"workspace_id": workspace.pk, "post_id": post.pk})
    response = client.get(url, {"tab": "publish"})
    assert response.status_code == 200
    assert response.context["publish_channels"][0]["scheduled_at"] is not None
    assert response.context["publish_channels"][0]["scheduled_label"].endswith("CET")
    assert b"Scheduled publication" in response.content
    assert b"data-copy-caption" in response.content
    assert b"innerHTML" not in response.content
