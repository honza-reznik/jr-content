from datetime import UTC, timedelta
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import pytest
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
    return create_content(
        workspace,
        "Publish test content",
        user,
        social_account_ids=workspace.social_accounts.filter(jrc_channel__is_enabled=True).values_list("pk", flat=True),
    )


def approved_post(post):
    variant = post.platform_posts.get()
    variant.platform_specific_caption = "A saved caption"
    variant.save(update_fields=["platform_specific_caption", "updated_at"])
    transition_platform_post(variant, PlatformPost.Status.PENDING_REVIEW)
    variant.refresh_from_db()
    transition_platform_post(variant, PlatformPost.Status.APPROVED)
    return variant


@pytest.mark.parametrize("role", ["owner", "client"])
def test_published_video_keeps_final_version_and_download(client, workspace, user, post, role):
    import uuid

    variant = approved_post(post)
    video = VideoAsset.objects.create(platform_post=variant, ff_asset_id=uuid.uuid4())
    version = VideoVersion.objects.create(
        video=video, number=2, ff_version_id=uuid.uuid4(), status=VideoVersion.Status.READY
    )
    video.final_version = version
    video.save(update_fields=["final_version"])
    WorkspaceMembership.objects.filter(workspace=workspace, user=user).update(workspace_role=role)
    client.force_login(user)
    mark = reverse("jrc_production:mark_published", kwargs={"workspace_id": workspace.pk, "post_id": post.pk})
    assert client.post(mark, {"platform_post": variant.pk}).status_code == 302
    detail = reverse("jrc_production:content_detail", kwargs={"workspace_id": workspace.pk, "post_id": post.pk})
    page = client.get(detail, {"tab": "publish"})
    assert b"Published manually" in page.content
    assert b"Final video \xc2\xb7 v2" in page.content
    assert b"Download final video" in page.content
    download = reverse(
        "jrc_video:final_download",
        kwargs={"workspace_id": workspace.pk, "post_id": post.pk, "platform_post_id": variant.pk},
    )
    assert download.encode() in page.content
    with patch("apps.jrc_video.uploads._call", return_value={"url": "http://testserver/files/video/raw/final.mp4"}):
        assert client.get(download).status_code == 302


def test_publish_without_date_offers_local_form_and_confirmation(client, workspace, user, post):
    variant = approved_post(post)
    client.force_login(user)
    detail = reverse("jrc_production:content_detail", kwargs={"workspace_id": workspace.pk, "post_id": post.pk})
    page = client.get(detail, {"tab": "publish"}).content.decode()
    assert 'name="action" value="set_date"' in page
    assert 'type="datetime-local"' in page
    assert f'name="platform_post" value="{variant.pk}"' in page
    assert "set_date=1" not in page
    assert "Set date" in page and "data-confirm-publication=" in page


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


def test_final_download_is_scoped_and_redirects_only_to_session_gateway(client, workspace, user, post):
    variant = approved_post(post)
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
    signed = "http://testserver/files/video/raw/file.mp4?filename=final.mp4"
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
    variant = approved_post(post)
    post.title = 'Žluťoučký / teaser "one"'
    post.save(update_fields=["title"])
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
    signed = "http://testserver/files/video/raw/file.mp4?filename=final.mp4"
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
    variant = approved_post(post)
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
            return_value={"url": "http://testserver/files/video/raw/file.mp4?filename=final.mp4"},
        ) as call,
    ):
        response = client.get(url)
        assert response.status_code == 302
        call.assert_called_once()

    detail = reverse("jrc_production:content_detail", kwargs={"workspace_id": workspace.pk, "post_id": post.pk})
    response = client.get(detail, {"tab": "publish"})
    assert response.status_code == 200
    assert url.encode() in response.content


@pytest.mark.parametrize(
    "unsafe_url",
    [
        "http://127.0.0.1:14133/file.mp4?X-Amz-Expires=600&X-Amz-Signature=secret",
        "http://testserver/files/video/raw/file.mp4?token=secret",
        "http://testserver/files/video/raw/file.mp4?X-Amz-Signature=secret",
    ],
)
def test_final_download_rejects_foreign_channel_and_freeframe_token_url(
    client, workspace, organization, user, post, unsafe_url
):
    from apps.composer.models import Post

    own_variant = approved_post(post)
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
    with (
        override_settings(JRC_S3_PUBLIC_ORIGIN="http://127.0.0.1:14133"),
        patch("apps.jrc_video.uploads._call", return_value={"url": unsafe_url}),
    ):
        response = client.get(
            reverse(
                "jrc_video:final_download",
                kwargs={"workspace_id": workspace.pk, "post_id": post.pk, "platform_post_id": own_variant.pk},
            )
        )
    assert response.status_code == 502
    assert b"secret" not in response.content


def test_final_download_hides_a_foreign_workspace_as_not_found(client, workspace, organization, user):
    from apps.composer.models import Post

    foreign_workspace = Workspace.objects.create(organization=organization, name="Unrelated client")
    channel = create_manual_channel(foreign_workspace, "instagram", "Foreign channel")
    foreign_post = Post.objects.create(workspace=foreign_workspace, title="Private content")
    variant = PlatformPost.objects.create(post=foreign_post, social_account=channel)
    client.force_login(user)
    url = reverse(
        "jrc_video:final_download",
        kwargs={"workspace_id": foreign_workspace.pk, "post_id": foreign_post.pk, "platform_post_id": variant.pk},
    )

    assert client.get(url).status_code == 404


@pytest.mark.parametrize("invalid_state", ["unapproved", "empty-caption", "not-ready"])
def test_final_download_requires_approved_caption_and_ready_final(client, workspace, user, post, invalid_state):
    variant = approved_post(post)
    video = VideoAsset.objects.create(platform_post=variant, ff_asset_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
    version = VideoVersion.objects.create(
        video=video,
        number=1,
        ff_version_id="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        status=VideoVersion.Status.READY,
    )
    video.final_version = version
    video.save(update_fields=["final_version"])
    if invalid_state == "unapproved":
        PlatformPost.objects.filter(pk=variant.pk).update(status=PlatformPost.Status.PENDING_REVIEW)
    elif invalid_state == "empty-caption":
        PlatformPost.objects.filter(pk=variant.pk).update(platform_specific_caption="")
        post.caption = ""
        post.save(update_fields=["caption"])
    else:
        version.status = VideoVersion.Status.PROCESSING
        version.save(update_fields=["status"])

    client.force_login(user)
    download = reverse(
        "jrc_video:final_download",
        kwargs={"workspace_id": workspace.pk, "post_id": post.pk, "platform_post_id": variant.pk},
    )
    with patch("apps.jrc_video.uploads._call") as call:
        assert client.get(download).status_code == 404
        call.assert_not_called()

    detail = reverse("jrc_production:content_detail", kwargs={"workspace_id": workspace.pk, "post_id": post.pk})
    assert download.encode() not in client.get(detail, {"tab": "publish"}).content


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


@pytest.mark.parametrize("role", ["owner", "manager"])
def test_set_date_persists_freeframe_video_and_preserves_other_channels(client, workspace, user, post, role):
    import uuid
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from apps.publisher.engine import PublishEngine

    workspace.timezone = "Europe/Prague"
    workspace.approval_workflow_mode = "required_internal_and_client"
    workspace.save(update_fields=["timezone", "approval_workflow_mode"])
    WorkspaceMembership.objects.filter(workspace=workspace, user=user).update(workspace_role=role)
    variant = approved_post(post)
    video = VideoAsset.objects.create(platform_post=variant, ff_asset_id=uuid.uuid4())
    version = VideoVersion.objects.create(video=video, number=1, ff_version_id=uuid.uuid4(), status="ready")
    video.final_version = version
    video.save(update_fields=["final_version"])
    other_channel = create_manual_channel(workspace, "linkedin_personal", "Unchanged channel")
    other = PlatformPost.objects.create(
        post=post,
        social_account=other_channel,
        status="scheduled",
        scheduled_at=timezone.now() + timedelta(days=5),
        platform_specific_caption="Keep this",
    )
    before = PlatformPost.objects.filter(pk=other.pk).values().get()
    assert not post.media_attachments.exists(), "Video is stored in FreeFrame, not PostMedia"
    detail = reverse("jrc_production:content_detail", kwargs={"workspace_id": workspace.pk, "post_id": post.pk})
    client.force_login(user)
    local = (timezone.now() + timedelta(days=2)).astimezone(ZoneInfo("Europe/Prague")).replace(second=0, microsecond=0)
    response = client.post(
        detail + "?tab=publish",
        {"action": "set_date", "platform_post": variant.pk, "scheduled_at": local.strftime("%Y-%m-%dT%H:%M")},
    )
    assert response.status_code == 302
    variant.refresh_from_db()
    post.refresh_from_db()
    assert variant.status == "scheduled"
    assert variant.scheduled_at == local.astimezone(UTC)
    assert post.scheduled_at == variant.scheduled_at
    assert PlatformPost.objects.filter(pk=other.pk).values().get() == before
    assert video.final_version_id == version.pk
    # A second submission can change just this copy's term.
    later = local + timedelta(days=1)
    assert (
        client.post(
            detail,
            {"action": "set_date", "platform_post": variant.pk, "scheduled_at": later.strftime("%Y-%m-%dT%H:%M")},
        ).status_code
        == 302
    )
    variant.refresh_from_db()
    assert variant.scheduled_at == later.astimezone(UTC)
    assert PlatformPost.objects.filter(pk=other.pk).values().get() == before
    with patch("apps.publisher.engine.timezone.now", return_value=datetime.now(UTC) + timedelta(days=10)):
        assert variant.pk not in {item.pk for item in PublishEngine()._get_due_platform_posts()}
    assert client.get(detail, {"tab": "publish"}).context["publish_channels"][0]["scheduled_at"] is not None


@pytest.mark.parametrize("role", ["editor", "client", "viewer"])
def test_set_date_uses_scheduling_permission(client, workspace, user, post, role):
    variant = approved_post(post)
    WorkspaceMembership.objects.filter(workspace=workspace, user=user).update(workspace_role=role)
    client.force_login(user)
    detail = reverse("jrc_production:content_detail", kwargs={"workspace_id": workspace.pk, "post_id": post.pk})
    assert b">Set date<" not in client.get(detail, {"tab": "publish"}).content
    assert (
        client.post(
            detail, {"action": "set_date", "platform_post": variant.pk, "scheduled_at": "2099-01-01T12:00"}
        ).status_code
        == 403
    )
    variant.refresh_from_db()
    assert variant.scheduled_at is None and variant.status == "approved"


@pytest.mark.parametrize("value", ["", "not-a-date", "2020-01-01T12:00", "2099-03-29T02:30", "2099-10-25T02:30"])
def test_set_date_rejects_invalid_past_or_dst_ambiguous_time(client, workspace, user, post, value):
    variant = approved_post(post)
    workspace.timezone = "Europe/Prague"
    workspace.save(update_fields=["timezone"])
    client.force_login(user)
    detail = reverse("jrc_production:content_detail", kwargs={"workspace_id": workspace.pk, "post_id": post.pk})
    response = client.post(detail, {"action": "set_date", "platform_post": variant.pk, "scheduled_at": value})
    assert response.status_code == 200
    assert response.context["publish_channels"][0]["date_form"].errors
    assert b'role="alert"' in response.content
    variant.refresh_from_db()
    assert variant.scheduled_at is None


@pytest.mark.parametrize("state", ["draft", "pending_review", "pending_client", "published"])
def test_set_date_rejects_unapproved_or_published_copy(client, workspace, user, post, state):
    variant = post.platform_posts.get()
    PlatformPost.objects.filter(pk=variant.pk).update(status=state)
    client.force_login(user)
    detail = reverse("jrc_production:content_detail", kwargs={"workspace_id": workspace.pk, "post_id": post.pk})
    response = client.post(
        detail, {"action": "set_date", "platform_post": variant.pk, "scheduled_at": "2099-01-01T12:00"}
    )
    assert response.status_code == 200
    assert b"Only approved or scheduled" in response.content
    variant.refresh_from_db()
    assert variant.scheduled_at is None and variant.status == state


def test_set_date_rejects_foreign_copy_and_archived_workspace(client, workspace, user, post, organization):
    from apps.composer.models import Post

    variant = approved_post(post)
    foreign_workspace = Workspace.objects.create(organization=organization, name="Foreign")
    foreign_post = Post.objects.create(workspace=foreign_workspace, title="Foreign")
    foreign_channel = create_manual_channel(foreign_workspace, "instagram", "Foreign")
    foreign_variant = PlatformPost.objects.create(post=foreign_post, social_account=foreign_channel, status="approved")
    client.force_login(user)
    detail = reverse("jrc_production:content_detail", kwargs={"workspace_id": workspace.pk, "post_id": post.pk})
    data = {"action": "set_date", "platform_post": foreign_variant.pk, "scheduled_at": "2099-01-01T12:00"}
    assert client.post(detail, data).status_code == 404
    workspace.is_archived = True
    workspace.save(update_fields=["is_archived"])
    data["platform_post"] = variant.pk
    assert client.post(detail, data).status_code == 404
    assert not post.platform_posts.exclude(scheduled_at__isnull=True).exists()


@pytest.mark.parametrize("channel_state", ["disabled", "automatic", "no-profile"])
def test_set_date_rejects_non_manual_or_disabled_channels(client, workspace, user, post, channel_state):
    variant = approved_post(post)
    profile = variant.social_account.jrc_channel
    if channel_state == "disabled":
        profile.is_enabled = False
        profile.save(update_fields=["is_enabled"])
    elif channel_state == "automatic":
        profile.kind = "connected"
        profile.save(update_fields=["kind"])
    else:
        profile.delete()
    client.force_login(user)
    detail = reverse("jrc_production:content_detail", kwargs={"workspace_id": workspace.pk, "post_id": post.pk})
    assert b">Set date<" not in client.get(detail, {"tab": "publish"}).content
    assert (
        client.post(
            detail, {"action": "set_date", "platform_post": variant.pk, "scheduled_at": "2099-01-01T12:00"}
        ).status_code
        == 200
    )
    variant.refresh_from_db()
    assert variant.scheduled_at is None and variant.status == "approved"


@pytest.mark.parametrize("target", ["not-a-uuid", "", "11111111-1111-4111-8111-111111111111"])
def test_set_date_invalid_copy_returns_404(client, workspace, user, post, target):
    approved_post(post)
    client.force_login(user)
    detail = reverse("jrc_production:content_detail", kwargs={"workspace_id": workspace.pk, "post_id": post.pk})
    assert (
        client.post(
            detail, {"action": "set_date", "platform_post": target, "scheduled_at": "2099-01-01T12:00"}
        ).status_code
        == 404
    )


def test_set_date_respects_custom_scheduling_grant_and_removed_membership(client, workspace, user, post):
    variant = approved_post(post)
    custom = CustomRole.objects.create(
        organization=workspace.organization, name="Date planner", permissions={"publish_directly": True}
    )
    WorkspaceMembership.objects.filter(workspace=workspace, user=user).update(
        workspace_role="editor", custom_role=custom
    )
    client.force_login(user)
    detail = reverse("jrc_production:content_detail", kwargs={"workspace_id": workspace.pk, "post_id": post.pk})
    data = {"action": "set_date", "platform_post": variant.pk, "scheduled_at": "2099-01-01T12:00"}
    assert client.post(detail, data).status_code == 302
    variant.refresh_from_db()
    assert variant.status == "scheduled"
    WorkspaceMembership.objects.filter(workspace=workspace, user=user).delete()
    assert client.post(detail, data).status_code == 404
