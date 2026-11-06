"""P1-B10: image bytes, channel history, live permissions and all three screens."""

from io import BytesIO
from unittest.mock import Mock, patch
from uuid import uuid4

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from apps.composer.models import PlatformPost, Post
from apps.composer.services import ChannelRemovalError, remove_platform_posts
from apps.jrc_channels.services import create_manual_channel
from apps.jrc_storage import views as files
from apps.jrc_texts.models import TextDocument, TextVersion
from apps.jrc_thumbnails import services
from apps.jrc_thumbnails.models import ChannelThumbnail
from apps.jrc_video.models import VideoAsset, VideoVersion
from apps.members.models import CustomRole, WorkspaceMembership
from apps.workspaces.models import Workspace

pytestmark = pytest.mark.django_db


def image_upload(fmt="PNG", size=(32, 24)):
    stream = BytesIO()
    Image.new("RGB", size, "orange").save(stream, format=fmt)
    mime, extension = services.FORMATS[fmt]
    return SimpleUploadedFile(f"photo.{extension}", stream.getvalue(), content_type=mime)


def upload(post, variant, user, image=None):
    return services.upload_thumbnail(
        post, variant, user, image or image_upload(), services.member(post.workspace, user)
    )


def url(action, post, variant, thumbnail=None):
    kwargs = {"workspace_id": post.workspace_id, "post_id": post.pk, "platform_post_id": variant.pk}
    if thumbnail is not None:
        kwargs["thumbnail_id"] = thumbnail.pk
    return reverse(f"jrc_thumbnails:{action}", kwargs=kwargs)


def detail(post):
    return reverse("jrc_production:content_detail", kwargs={"workspace_id": post.workspace_id, "post_id": post.pk})


@pytest.fixture
def gateway(monkeypatch):
    client = Mock()
    client.head_object.return_value = {"ContentLength": 10}
    client.get_object.side_effect = lambda **kwargs: {"Body": BytesIO(b"0123456789")}
    monkeypatch.setattr(files, "_storage", lambda: (client, "jrc-app"))
    monkeypatch.setattr(files, "acquire", lambda user: lambda: None)
    return client


@pytest.fixture
def downloadable(variant):
    variant.status = PlatformPost.Status.APPROVED
    variant.platform_specific_caption = "A saved caption"
    variant.save(update_fields=["status", "platform_specific_caption"])
    video = VideoAsset.objects.create(platform_post=variant, ff_asset_id=uuid4())
    video.final_version = VideoVersion.objects.create(
        video=video, number=1, ff_version_id=uuid4(), status=VideoVersion.Status.READY
    )
    video.save(update_fields=["final_version"])
    return video


@pytest.mark.parametrize("fmt", ["JPEG", "PNG", "WEBP"])
def test_supported_images_are_stored_with_verified_metadata(post, variant, user, fmt):
    picture = image_upload(fmt)
    data = picture.read()
    picture.seek(0)
    thumbnail = upload(post, variant, user, picture)
    assert (thumbnail.number, thumbnail.width, thumbnail.height, thumbnail.size) == (1, 32, 24, len(data))
    assert thumbnail.content_type == services.FORMATS[fmt][0]
    assert thumbnail.image.name.startswith(f"jrc_thumbnails/{post.workspace_id}/{variant.pk}/")
    assert thumbnail.image.name.endswith("." + services.FORMATS[fmt][1])
    assert thumbnail.uploaded_by == user
    with thumbnail.image.open("rb") as stored:
        assert stored.read() == data


def test_replacement_keeps_history_and_channels_independent(post, variant, workspace, user):
    other = PlatformPost.objects.create(post=post, social_account=create_manual_channel(workspace, "youtube", "Other"))
    first = upload(post, variant, user)
    second = upload(post, variant, user, image_upload("WEBP"))
    separate = upload(post, other, user)
    assert [v.pk for v in variant.jrc_thumbnails.all()] == [second.pk, first.pk]
    assert separate.number == 1
    assert separate.platform_post == other
    with first.image.open("rb") as previous:
        assert previous.read().startswith(b"\x89PNG")


@pytest.mark.parametrize(
    "invalid", ["missing", "empty", "html", "gif", "truncated-jpeg", "truncated-png", "oversize", "pixels", "type"]
)
def test_invalid_upload_creates_no_version_or_file(post, variant, user, invalid, monkeypatch):
    picture = image_upload()
    if invalid == "missing":
        picture = None
    elif invalid == "empty":
        picture = SimpleUploadedFile("a.png", b"", content_type="image/png")
    elif invalid == "html":
        picture = SimpleUploadedFile("a.png", b"<html>fake image</html>", content_type="image/png")
    elif invalid == "gif":
        stream = BytesIO()
        Image.new("RGB", (2, 2)).save(stream, format="GIF")
        picture = SimpleUploadedFile("a.png", stream.getvalue(), content_type="image/png")
    elif invalid.startswith("truncated"):
        original = image_upload("JPEG" if invalid.endswith("jpeg") else "PNG")
        data = original.read()
        picture = SimpleUploadedFile(original.name, data[:-15], content_type=original.content_type)
    elif invalid == "oversize":
        picture.size = services.MAX_BYTES + 1
    elif invalid == "pixels":
        monkeypatch.setattr(services, "MAX_PIXELS", 10)
    elif invalid == "type":
        picture.name, picture.content_type = "a.txt", "text/plain"
    with pytest.raises(ValidationError):
        services.upload_thumbnail(post, variant, user, picture, services.member(post.workspace, user))
    assert not ChannelThumbnail.objects.exists()


def test_exact_size_limit_and_canonical_extension(post, variant, user):
    picture = image_upload("PNG")
    picture.size = services.MAX_BYTES
    picture.name, picture.content_type = "phone.jpg", "image/jpeg"
    result = upload(post, variant, user, picture)
    assert result.content_type == "image/png"
    assert result.image.name.endswith(".png")


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
def test_upload_permissions_and_controls(client, post, variant, workspace, user, role, own, allowed):
    WorkspaceMembership.objects.filter(workspace=workspace, user=user).update(workspace_role=role)
    if not own:
        post.author = None
        post.save(update_fields=["author"])
    client.force_login(user)
    response = client.post(url("upload", post, variant), {"image": image_upload(), "tab": "publish"})
    assert response.status_code == (302 if allowed else 403)
    assert ChannelThumbnail.objects.exists() == allowed
    for tab in ("caption", "publish"):
        response = client.get(detail(post), {"tab": tab})
        assert response.status_code == 200
        assert (b"data-thumbnail-form" in response.content) == allowed
        if allowed:
            assert b'accept="image/jpeg,image/png,image/webp,.jpg,.jpeg,.png,.webp"' in response.content


@pytest.mark.parametrize(
    "role,allowed",
    [
        ("owner", True),
        ("manager", True),
        ("editor", False),
        ("contributor", False),
        ("client", True),
        ("viewer", False),
    ],
)
def test_download_reuses_publication_permissions(
    client, post, variant, workspace, user, gateway, downloadable, role, allowed
):
    thumbnail = upload(post, variant, user)
    WorkspaceMembership.objects.filter(workspace=workspace, user=user).update(workspace_role=role)
    client.force_login(user)
    response = client.get(url("download", post, variant, thumbnail))
    assert response.status_code == (200 if allowed else 403)
    if allowed:
        assert response["Content-Disposition"].startswith('attachment; filename="plant-care-')
        assert response["Content-Type"] == "image/png"
        assert response["Cache-Control"] == "private, no-store"
        assert b"".join(response.streaming_content) == b"0123456789"
    else:
        gateway.head_object.assert_not_called()
    content = client.get(detail(post), {"tab": "publish"}).content
    assert (b"data-thumbnail-download" in content) == allowed
    assert b"data-thumbnail-image" in content
    # Everyone in this workspace, including a viewer, may see the image.
    response = client.get(reverse("jrc_private_file", kwargs={"path": thumbnail.image.name}))
    assert response.status_code == 200
    response.close()


@pytest.mark.parametrize(
    "role,permission,allowed",
    [("editor", False, False), ("contributor", True, True), ("client", False, True), ("viewer", True, False)],
)
def test_custom_role_download_permissions(
    client, post, variant, workspace, organization, user, gateway, downloadable, role, permission, allowed
):
    thumbnail = upload(post, variant, user)
    custom = CustomRole.objects.create(
        organization=organization, name="Custom", permissions={"publish_directly": permission}
    )
    WorkspaceMembership.objects.filter(workspace=workspace, user=user).update(workspace_role=role, custom_role=custom)
    client.force_login(user)
    response = client.get(url("download", post, variant, thumbnail))
    assert response.status_code == (200 if allowed else 403)
    response.close()


@pytest.mark.parametrize("role", ["owner", "client"])
@pytest.mark.parametrize(
    "missing",
    [
        "draft",
        "pending_review",
        "rejected",
        "empty-caption",
        "whitespace-caption",
        "empty-saved-caption",
        "no-video",
        "no-final",
        "uploading",
        "processing",
        "failed",
    ],
)
def test_download_conditions_match_final_video(
    client, post, variant, workspace, user, gateway, downloadable, role, missing
):
    previous = upload(post, variant, user)
    current = upload(post, variant, user)
    if missing in {"draft", "pending_review", "rejected"}:
        variant.status = missing
        variant.save(update_fields=["status"])
        if missing == "draft":
            # P1-B27: a draft is downloadable where the mode does not require approval
            # (test_approval_modes); here the mode requires it.
            workspace.approval_workflow_mode = "required_internal"
            workspace.save(update_fields=["approval_workflow_mode"])
    elif missing in {"empty-caption", "whitespace-caption"}:
        variant.platform_specific_caption = "" if missing == "empty-caption" else " \n\t "
        variant.save(update_fields=["platform_specific_caption"])
    elif missing == "empty-saved-caption":
        document = TextDocument.objects.create(
            workspace=workspace, post=post, platform_post=variant, kind="caption", format="plain"
        )
        TextVersion.objects.create(
            document=document, number=1, state="final", content={}, text=" \n ", finalized_at=timezone.now()
        )
    elif missing == "no-video":
        downloadable.delete()
    elif missing == "no-final":
        downloadable.final_version = None
        downloadable.save(update_fields=["final_version"])
    else:
        downloadable.final_version.status = missing
        downloadable.final_version.save(update_fields=["status"])
    WorkspaceMembership.objects.filter(workspace=workspace, user=user).update(workspace_role=role)
    client.force_login(user)
    final_url = reverse(
        "jrc_video:final_download",
        kwargs={"workspace_id": workspace.pk, "post_id": post.pk, "platform_post_id": variant.pk},
    )
    with patch("apps.jrc_video.uploads._call") as video_service:
        assert client.get(final_url).status_code == 404
        for thumbnail in (previous, current):
            response = client.get(url("download", post, variant, thumbnail))
            assert response.status_code == 404
        video_service.assert_not_called()
    gateway.head_object.assert_not_called()
    gateway.get_object.assert_not_called()
    for tab in ("caption", "publish"):
        response = client.get(detail(post), {"tab": tab})
        assert response.status_code == 200
        assert b"data-thumbnail-image" in response.content
        assert b"data-thumbnail-download" not in response.content
        if tab == "publish":
            assert b"data-thumbnail-blocked>Download thumbnail</button>" in response.content
            assert b'disabled aria-describedby="thumbnail-download-reason-' in response.content
            # P1-B27: the reason names what is actually missing, from the same rule as the download.
            assert b"To download it: " in response.content
        else:
            assert b"data-thumbnail-blocked" not in response.content
        assert url("download", post, variant, previous).encode() not in response.content
        assert final_url.encode() not in response.content


@pytest.mark.parametrize("role", ["owner", "client"])
@pytest.mark.parametrize(
    "state", [PlatformPost.Status.APPROVED, PlatformPost.Status.SCHEDULED, PlatformPost.Status.PUBLISHED]
)
@pytest.mark.parametrize("caption_source", ["channel", "post", "saved"])
def test_ready_downloads_and_buttons_match_final_video(
    client, post, variant, workspace, user, gateway, downloadable, role, state, caption_source
):
    previous = upload(post, variant, user)
    current = upload(post, variant, user)
    variant.status = state
    if caption_source == "post":
        post.caption = "Fallback caption"
        post.save(update_fields=["caption"])
        variant.platform_specific_caption = None
    elif caption_source == "saved":
        variant.platform_specific_caption = ""
        document = TextDocument.objects.create(
            workspace=workspace, post=post, platform_post=variant, kind="caption", format="plain"
        )
        TextVersion.objects.create(
            document=document, number=1, state="final", content={}, text="Saved caption", finalized_at=timezone.now()
        )
    variant.save(update_fields=["status", "platform_specific_caption"])
    WorkspaceMembership.objects.filter(workspace=workspace, user=user).update(workspace_role=role)
    client.force_login(user)
    final_url = reverse(
        "jrc_video:final_download",
        kwargs={"workspace_id": workspace.pk, "post_id": post.pk, "platform_post_id": variant.pk},
    )
    with patch("apps.jrc_video.uploads._call", return_value={"url": "http://testserver/files/video/raw/final.mp4"}):
        assert client.get(final_url).status_code == 302
    for thumbnail in (previous, current):
        response = client.get(url("download", post, variant, thumbnail))
        assert response.status_code == 200
        assert b"".join(response.streaming_content) == b"0123456789"
    for tab in ("caption", "publish"):
        content = client.get(detail(post), {"tab": tab}).content
        assert url("download", post, variant, current).encode() in content
        assert url("download", post, variant, previous).encode() in content
        if tab == "publish":
            assert final_url.encode() in content


@pytest.mark.parametrize("state", ["revoked", "archived", "deleted-organization", "inactive"])
def test_stale_links_cannot_read_storage(client, post, variant, workspace, organization, user, gateway, state):
    thumbnail = upload(post, variant, user)
    client.force_login(user)
    if state == "revoked":
        workspace.memberships.all().delete()
    elif state == "archived":
        workspace.is_archived = True
        workspace.save()
    elif state == "deleted-organization":
        organization.deleted_at = timezone.now()
        organization.save()
    else:
        user.is_active = False
        user.save()
    for address in (
        url("download", post, variant, thumbnail),
        reverse("jrc_private_file", kwargs={"path": thumbnail.image.name}),
    ):
        response = client.get(address)
        assert response.status_code in (302, 403, 404)
    assert client.post(url("upload", post, variant), {"image": image_upload()}).status_code in (302, 404)
    gateway.head_object.assert_not_called()
    assert ChannelThumbnail.objects.count() == 1


def test_foreign_workspace_and_mixed_identifiers_are_404(client, post, variant, workspace, organization, user, gateway):
    thumbnail = upload(post, variant, user)
    foreign = Workspace.objects.create(organization=organization, name="Private workspace")
    foreign_post = Post.objects.create(workspace=foreign, title="Other")
    foreign_variant = PlatformPost.objects.create(
        post=foreign_post, social_account=create_manual_channel(foreign, "youtube", "Other")
    )
    client.force_login(user)
    assert client.post(url("upload", foreign_post, foreign_variant), {"image": image_upload()}).status_code == 404
    assert client.get(url("download", foreign_post, foreign_variant, thumbnail)).status_code == 404
    assert client.post(url("upload", post, foreign_variant), {"image": image_upload()}).status_code == 404
    assert client.get(url("download", post, foreign_variant, thumbnail)).status_code == 404
    with pytest.raises(ValidationError):
        upload(post, foreign_variant, user)
    member = WorkspaceMembership.objects.create(workspace=foreign, user=user, workspace_role="owner")
    with pytest.raises(PermissionDenied):
        services.upload_thumbnail(post, variant, user, image_upload(), member)
    gateway.head_object.assert_not_called()


def test_cross_channel_thumbnail_download_is_404(client, post, variant, workspace, user, gateway):
    thumbnail = upload(post, variant, user)
    other = PlatformPost.objects.create(post=post, social_account=create_manual_channel(workspace, "youtube", "Other"))
    client.force_login(user)
    assert client.get(url("download", post, other, thumbnail)).status_code == 404
    gateway.head_object.assert_not_called()


def test_anonymous_and_wrong_methods(client, post, variant, user, gateway):
    thumbnail = upload(post, variant, user)
    assert client.post(url("upload", post, variant), {"image": image_upload()}).status_code == 302
    assert client.get(url("download", post, variant, thumbnail)).status_code == 302
    client.force_login(user)
    assert client.get(url("upload", post, variant)).status_code == 405
    assert client.post(url("download", post, variant, thumbnail)).status_code == 405
    gateway.head_object.assert_not_called()


def test_validation_error_and_return_tab_are_safe(client, post, variant, user):
    client.force_login(user)
    response = client.post(url("upload", post, variant), {"tab": "https://external.invalid"}, follow=True)
    assert response.status_code == 200
    assert response.redirect_chain[0][0].endswith(f"?tab=caption#thumbnail-{variant.pk}")
    assert b"Choose an image to upload" in response.content
    assert not ChannelThumbnail.objects.exists()


def test_screens_include_current_and_complete_history(client, post, variant, user):
    thumbnail = upload(post, variant, user)
    # History must not silently hide the first versions once there are more than 20.
    ChannelThumbnail.objects.bulk_create(
        [
            ChannelThumbnail(
                post=post,
                platform_post=variant,
                number=n,
                image=f"jrc_thumbnails/{n}.png",
                content_type="image/png",
                size=10,
                width=1,
                height=1,
            )
            for n in range(2, 24)
        ]
    )
    client.force_login(user)
    for tab in ("caption", "publish"):
        content = client.get(detail(post), {"tab": tab}).content
        assert b"Version 23" in content
        assert b"Earlier versions (22)" in content
        assert thumbnail.image.url.encode() in content
    approval = client.get(detail(post), {"tab": "approval"}).content
    assert b"Thumbnail for Instagram" in approval
    assert b"version 23" in approval
    assert b"data-thumbnail-form" not in approval


def test_channel_removal_preserves_thumbnail_history_and_parent_can_be_deleted(post, variant, user):
    upload(post, variant, user)
    with pytest.raises(ChannelRemovalError, match="thumbnails"):
        remove_platform_posts(post.platform_posts.all())
    assert ChannelThumbnail.objects.count() == 1
    assert PlatformPost.objects.filter(pk=variant.pk).exists()
    post.delete()
    assert not ChannelThumbnail.objects.exists()


@pytest.mark.parametrize(
    "role,own,allowed",
    [
        ("editor", False, True),
        ("contributor", True, True),
        ("contributor", False, False),
        ("client", True, False),
        ("viewer", True, False),
    ],
)
def test_custom_role_cannot_grant_client_or_viewer_upload(
    client, post, variant, workspace, organization, user, role, own, allowed
):
    custom = CustomRole.objects.create(
        organization=organization, name="Text editor", permissions={"create_posts": True, "edit_others_posts": True}
    )
    WorkspaceMembership.objects.filter(workspace=workspace, user=user).update(workspace_role=role, custom_role=custom)
    if not own:
        post.author = None
        post.save(update_fields=["author"])
    client.force_login(user)
    assert client.post(url("upload", post, variant), {"image": image_upload()}).status_code == (302 if allowed else 403)


@pytest.mark.django_db(transaction=True)
def test_concurrent_uploads_keep_distinct_sequential_versions(post, variant, user):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from django.db import close_old_connections

    barrier = Barrier(2)

    def save_one():
        close_old_connections()
        try:
            barrier.wait(timeout=20)
            return upload(post, variant, user).number
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as pool:
        numbers = list(pool.map(lambda _: save_one(), range(2)))
    assert sorted(numbers) == [1, 2]
    assert list(variant.jrc_thumbnails.values_list("number", flat=True)) == [2, 1]


@pytest.mark.parametrize("role", ["owner", "client"])
def test_published_thumbnail_download_remains_available(
    client, post, variant, user, workspace, downloadable, gateway, role
):
    from apps.jrc_production.services import mark_published_manually

    thumbnail = upload(post, variant, user)
    WorkspaceMembership.objects.filter(workspace=workspace, user=user).update(workspace_role=role)
    mark_published_manually(post, variant.pk, user)
    client.force_login(user)
    page = client.get(detail(post), {"tab": "publish"})
    assert b"Published manually" in page.content
    assert b"Copy caption" in page.content and b"Download final video" in page.content
    assert b"Download thumbnail" in page.content
    assert client.get(url("download", post, variant, thumbnail)).status_code == 200


def test_invalid_image_error_is_by_the_field_and_keeps_previous_version(client, post, variant, user):
    first = upload(post, variant, user)
    client.force_login(user)
    response = client.post(
        url("upload", post, variant),
        {
            "image": SimpleUploadedFile("video.mov", b"not an image", content_type="video/quicktime"),
        },
        follow=True,
    )
    page = response.content.decode()
    assert 'aria-invalid="true"' in page
    assert "data-thumbnail-status>Upload a JPG, PNG or WebP image." in page
    assert "up to 20 MB" in page
    assert list(ChannelThumbnail.objects.values_list("pk", flat=True)) == [first.pk]
