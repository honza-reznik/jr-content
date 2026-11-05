"""P1-B15: workspace accents and private, bounded custom channel icons."""

from io import BytesIO
from unittest.mock import Mock
from uuid import UUID

import pytest
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from PIL import Image

from apps.jrc_channels.icons import MAX_BYTES, validated_icon
from apps.jrc_channels.services import create_manual_channel
from apps.jrc_production.services import create_content
from apps.jrc_storage import views as files
from apps.members.models import WorkspaceMembership
from apps.workspaces.colors import PALETTE, validate_accent
from apps.workspaces.models import Workspace

pytestmark = pytest.mark.django_db


def url(workspace, action="color", account=None):
    kwargs = {"workspace_id": workspace.pk}
    if account:
        kwargs["account_id"] = account.pk
    return reverse(f"jrc_channels:{action}", kwargs=kwargs)


def image(fmt="PNG", name="icon.png", content_type="image/png", size=(256, 256)):
    data = BytesIO()
    Image.new("RGB", size, "#6D28D9").save(data, format=fmt)
    return SimpleUploadedFile(name, data.getvalue(), content_type)


@pytest.fixture
def owner(client, user, workspace):
    WorkspaceMembership.objects.create(workspace=workspace, user=user, workspace_role="owner")
    client.force_login(user)
    return client


@pytest.fixture
def local_storage(settings, tmp_path):
    settings.STORAGES = {**settings.STORAGES, "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"}}
    settings.MEDIA_ROOT = tmp_path


@pytest.mark.parametrize("value", ["#ffffff", "#AAAAAA", "#00FF00", "#fff", "red", "#000000;", "#12345g"])
def test_invalid_or_low_contrast_colour(value):
    with pytest.raises(ValidationError):
        validate_accent(value)


def test_palette_and_stable_default(workspace):
    assert len(PALETTE) == 10
    for value, _label in PALETTE:
        assert validate_accent(value) == value
    first = workspace.accent_color
    workspace.refresh_from_db()
    assert workspace.accent_color == first
    assert Workspace(id=UUID(int=1)).accent_color != Workspace(id=UUID(int=2)).accent_color
    workspace.primary_color = "#FFFFFF"
    assert workspace.accent_color == first
    workspace.primary_color = '" onload="bad'
    assert workspace.accent_color == first


@pytest.mark.parametrize("role", ["owner", "manager", "editor", "contributor", "viewer", "client"])
def test_colour_roles(client, user, workspace, role):
    WorkspaceMembership.objects.create(workspace=workspace, user=user, workspace_role=role)
    client.force_login(user)
    response = client.post(url(workspace), {"color": "#6d28d9"})
    workspace.refresh_from_db()
    if role in {"owner", "manager"}:
        assert response.status_code == 302
        assert workspace.primary_color == "#6D28D9"
    else:
        assert response.status_code == 403
        assert workspace.primary_color == ""
        html = client.get(url(workspace, "profile")).content.decode()
        assert "Save colour" not in html
        assert workspace.accent_color in html


def test_invalid_colour_keeps_previous_and_shows_error(owner, workspace):
    workspace.primary_color = "#1D4ED8"
    workspace.save()
    response = owner.post(url(workspace), {"color": "#FFFFFF"})
    assert b"contrast with white" in response.content
    workspace.refresh_from_db()
    assert workspace.primary_color == "#1D4ED8"
    assert b'value="#FFFFFF"' in response.content


@pytest.mark.parametrize(
    "fmt,extension,mime", [("PNG", "png", "image/png"), ("JPEG", "jpg", "image/jpeg"), ("WEBP", "webp", "image/webp")]
)
def test_raster_is_resized_and_reencoded(fmt, extension, mime):
    result = validated_icon(image(fmt, f"photo.{extension}", mime))
    with Image.open(result) as decoded:
        assert decoded.format == "PNG"
        assert decoded.size == (128, 128)
        assert not decoded.info


@pytest.mark.parametrize(
    "kind", ["svg", "gif", "spoof", "wrong_mime", "wrong_extension", "large", "pixels", "empty", "missing", "truncated"]
)
def test_bad_icon(kind):
    uploads = {
        "svg": lambda: SimpleUploadedFile("x.svg", b'<svg xmlns="http://www.w3.org/2000/svg"></svg>', "image/svg+xml"),
        "gif": lambda: image("GIF", "x.gif", "image/gif"),
        "spoof": lambda: SimpleUploadedFile("x.png", b"<script>bad</script>", "image/png"),
        "wrong_mime": lambda: image(content_type="text/html"),
        "wrong_extension": lambda: image(name="x.svg"),
        "large": lambda: SimpleUploadedFile("x.png", b"x" * (MAX_BYTES + 1), "image/png"),
        "pixels": lambda: image(size=(2001, 2000)),
        "empty": lambda: SimpleUploadedFile("x.png", b"", "image/png"),
        "missing": lambda: None,
        "truncated": lambda: SimpleUploadedFile(
            "x.jpg", image("JPEG", "x.jpg", "image/jpeg").read()[:-100], "image/jpeg"
        ),
    }
    with pytest.raises(ValidationError):
        validated_icon(uploads[kind]())


@pytest.mark.parametrize("role", ["owner", "manager", "editor", "contributor", "viewer", "client"])
def test_icon_roles(client, user, workspace, role, local_storage):
    WorkspaceMembership.objects.create(workspace=workspace, user=user, workspace_role=role)
    account = create_manual_channel(workspace, "custom", "Forendors")
    client.force_login(user)
    response = client.post(url(workspace, "icon", account), {"icon": image()})
    account.refresh_from_db()
    if role in {"owner", "manager"}:
        assert response.status_code == 302
        assert account.jrc_channel.icon
    else:
        assert response.status_code == 403
        assert not account.jrc_channel.icon


def test_replace_remove_and_reject_without_losing_icon(
    owner, workspace, local_storage, django_capture_on_commit_callbacks
):
    account = create_manual_channel(workspace, "custom", "Forendors")
    owner.post(url(workspace, "icon", account), {"icon": image()})
    account.refresh_from_db()
    first = account.jrc_channel.icon.name
    storage = account.jrc_channel.icon.storage
    assert storage.exists(first)
    owner.post(url(workspace, "icon", account), {"icon": SimpleUploadedFile("x.svg", b"bad", "image/svg+xml")})
    account.refresh_from_db()
    assert account.jrc_channel.icon.name == first
    with django_capture_on_commit_callbacks(execute=True):
        owner.post(url(workspace, "icon", account), {"icon": image()})
    account.refresh_from_db()
    second = account.jrc_channel.icon.name
    assert second != first
    assert not storage.exists(first)
    with django_capture_on_commit_callbacks(execute=True):
        owner.post(url(workspace, "icon", account), {"remove_icon": "1"})
    account.refresh_from_db()
    assert not account.jrc_channel.icon
    assert not storage.exists(second)


def test_icon_cannot_override_known_platform(owner, workspace):
    account = create_manual_channel(workspace, "instagram", "IG")
    assert owner.post(url(workspace, "icon", account), {"icon": image()}).status_code == 403


def test_foreign_workspace_and_anonymous(client, owner, workspace, organization):
    other = Workspace.objects.create(organization=organization, name="Other")
    account = create_manual_channel(other, "custom", "Hidden")
    assert owner.post(url(other), {"color": "#000000"}).status_code == 403
    assert owner.post(url(workspace, "icon", account), {"icon": image()}).status_code == 404
    assert owner.post(url(other, "icon", account), {"icon": image()}).status_code == 403
    owner.logout()
    assert client.post(url(workspace), {"color": "#000000"}).status_code == 302
    assert client.post(url(other, "icon", account), {"icon": image()}).status_code == 302


def test_private_icon_checks_membership_on_each_read(owner, workspace, user, organization, monkeypatch):
    account = create_manual_channel(workspace, "custom", "Forendors")
    key = f"jrc_channel_icons/{account.pk}/test.png"
    profile = account.jrc_channel
    profile.icon = key
    profile.save()
    storage = Mock()
    storage.head_object.return_value = {"ContentLength": 3}
    storage.get_object.return_value = {"Body": BytesIO(b"png")}
    monkeypatch.setattr(files, "_storage", lambda: (storage, "test"))
    monkeypatch.setattr(files, "acquire", lambda _user: lambda: None)
    file_url = reverse("jrc_private_file", kwargs={"path": key})
    response = owner.get(file_url)
    assert response.status_code == 200
    assert b"".join(response.streaming_content) == b"png"
    assert response["Cache-Control"] == "private, no-store"
    assert response["X-Content-Type-Options"] == "nosniff"
    assert owner.head(file_url).status_code == 200
    storage.reset_mock()
    WorkspaceMembership.objects.filter(user=user, workspace=workspace).delete()
    assert owner.get(file_url).status_code == 404
    storage.head_object.assert_not_called()
    owner.logout()
    assert owner.get(file_url).status_code == 403


def test_identity_on_board_detail_caption_publish_and_switcher(owner, user, workspace):
    workspace.primary_color = "#6D28D9"
    workspace.save()
    account = create_manual_channel(workspace, "custom", "Forendors")
    profile = account.jrc_channel
    profile.icon = f"jrc_channel_icons/{account.pk}/test.png"
    profile.save()
    post = create_content(workspace, "A test idea", user, social_account_ids=[account.pk])
    detail = reverse("jrc_production:content_detail", kwargs={"workspace_id": workspace.pk, "post_id": post.pk})
    for path in [
        reverse("jrc_production:board"),
        detail,
        detail + "?tab=caption",
        detail + "?tab=publish",
        reverse("calendar:calendar", kwargs={"workspace_id": workspace.pk}),
    ]:
        html = owner.get(path).content.decode()
        assert "#6D28D9" in html
        assert f'data-channel-icon="{account.pk}"' in html
    # Known platforms retain the existing Brightbean SVG, with or without a local profile.
    from django.template.loader import render_to_string

    ig = create_manual_channel(workspace, "instagram", "Instagram")
    assert "#E4405F" in render_to_string("jrc_channels/icon.html", {"account": ig})
    # Calendar chips keep the upstream white glyph on their platform-coloured background.
    facebook = create_manual_channel(workspace, "facebook", "Facebook")
    calendar_icon = render_to_string("jrc_channels/icon.html", {"account": facebook, "calendar_icon": True})
    assert 'fill="currentColor"' in calendar_icon
    assert 'fill="#1877F2"' not in calendar_icon
    profile.icon = ""
    profile.save()
    assert "<svg" in render_to_string("jrc_channels/icon.html", {"account": account})
    assert "<svg" in render_to_string("jrc_channels/icon.html", {"account": account, "calendar_icon": True})
