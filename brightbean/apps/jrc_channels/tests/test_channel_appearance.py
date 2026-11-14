"""P2-B12: shared channel colours, safe website icons and editing boundaries."""

import socket
from io import BytesIO
from unittest.mock import Mock, patch

import pytest
from django.core.exceptions import ValidationError
from django.test import Client, override_settings
from django.urls import reverse
from PIL import Image

from apps.jrc_channels import favicon_fetch as remote
from apps.jrc_channels.favicons import fetch_favicon, refresh_favicon
from apps.jrc_channels.icons import MAX_BYTES, validated_favicon
from apps.jrc_channels.services import create_manual_channel
from apps.jrc_channels.tasks import load_website_icon
from apps.jrc_production.services import create_content
from apps.jrc_ui.features import MVP_FEATURES
from apps.members.models import CustomRole, WorkspaceMembership
from apps.workspaces.colors import contrast_ratio
from apps.workspaces.models import Workspace

pytestmark = pytest.mark.django_db


@pytest.fixture
def owner(client, user, workspace):
    WorkspaceMembership.objects.create(workspace=workspace, user=user, workspace_role="owner")
    client.force_login(user)
    return client


@pytest.fixture
def local_storage(settings, tmp_path):
    settings.STORAGES = {**settings.STORAGES, "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"}}
    settings.MEDIA_ROOT = tmp_path


def url(workspace, account, action):
    return reverse(f"jrc_channels:{action}", kwargs={"workspace_id": workspace.pk, "account_id": account.pk})


def png(size=(32, 32), fmt="PNG"):
    stream = BytesIO()
    Image.new("RGB", size, "#B91C1C").save(stream, format=fmt)
    return stream.getvalue()


@pytest.mark.parametrize(
    "platform,expected",
    [
        ("instagram", "#E4405F"),
        ("facebook", "#1877F2"),
        ("youtube", "#FF0000"),
        ("tiktok", "#000000"),
        ("linkedin_personal", "#0A66C2"),
        ("custom", "#047857"),
    ],
)
def test_platform_defaults_and_contrast(workspace, platform, expected):
    account = create_manual_channel(workspace, platform, "Forendors")
    assert account.jrc_accent_color == expected
    assert contrast_ratio(expected, account.jrc_accent_text_color) >= 4.5
    account.jrc_channel.color = "#FFFFFF"
    account.jrc_channel.save()
    account.refresh_from_db()
    assert account.jrc_accent_color == "#FFFFFF"
    assert account.jrc_accent_text_color == "#000000"


def test_custom_stable_palette_and_legacy_safe_fallback(workspace):
    account = create_manual_channel(workspace, "custom", "Members")
    color = account.jrc_accent_color
    account.refresh_from_db()
    assert account.jrc_accent_color == color
    account.jrc_channel.color = '";background:url(bad)'
    assert account.jrc_accent_color == color


@pytest.mark.parametrize("role", ["owner", "manager", "editor", "contributor", "client", "viewer"])
def test_edit_appearance_permissions(owner, user, workspace, role, local_storage):
    membership = WorkspaceMembership.objects.get(workspace=workspace, user=user)
    membership.workspace_role = role
    membership.save()
    account = create_manual_channel(workspace, "custom", "Members")
    with patch("apps.jrc_channels.favicons.fetch_favicon", return_value=validated_favicon(png())) as fetch:
        for action, data in [("channel_color", {"color": "#ffffff"}), ("favicon", {})]:
            assert owner.post(url(workspace, account, action), data).status_code == (
                302 if role in {"owner", "manager"} else 403
            )
        assert fetch.call_count == (1 if role in {"owner", "manager"} else 0)
    account.refresh_from_db()
    assert account.jrc_channel.color == ("#FFFFFF" if role in {"owner", "manager"} else "")


def test_custom_permission_and_workspace_boundary(owner, user, workspace, organization):
    account = create_manual_channel(workspace, "custom", "Members")
    membership = WorkspaceMembership.objects.get(workspace=workspace, user=user)
    membership.custom_role = CustomRole.objects.create(
        organization=organization, name="Restricted", permissions={"manage_social_accounts": False}
    )
    membership.save()
    for action in ["channel_color", "favicon"]:
        assert owner.post(url(workspace, account, action), {"color": "#123456"}).status_code == 403
    membership.custom_role = None
    membership.save()
    other = Workspace.objects.create(organization=organization, name="Private")
    foreign = create_manual_channel(other, "custom", "Private")
    for action in ["channel_color", "favicon"]:
        assert owner.post(url(workspace, foreign, action), {"color": "#123456"}).status_code == 404
        assert owner.get(url(workspace, account, action)).status_code == 405
        secured = Client(enforce_csrf_checks=True)
        secured.force_login(user)
        assert secured.post(url(workspace, account, action), {}).status_code == 403


@pytest.mark.parametrize("color", ["#123456\n", "red", "#fff", '";bad', "", "#00000g"])
def test_invalid_channel_colour_preserves_value(owner, workspace, color):
    account = create_manual_channel(workspace, "custom", "Members")
    response = owner.post(url(workspace, account, "channel_color"), {"color": color})
    assert response.status_code == 200
    account.refresh_from_db()
    assert account.jrc_channel.color == ""
    assert response.context["channels"][0]["color_form"].errors


@pytest.mark.parametrize(
    "raw",
    [
        "http://127.0.0.1/",
        "https://10.0.0.1/",
        "http://169.254.169.254/",
        "http://[::1]/",
        "http://[::ffff:127.0.0.1]/",
        "http://[2002:7f00:1::]/",
        "http://[64:ff9b::7f00:1]/",
        "http://[64:ff9b:1::7f00:1]/",
        "file:///etc/passwd",
        "ftp://public.invalid/",
        "https://u:p@public.invalid/",
        "http://public.invalid:8080/",
    ],
)
def test_ssrf_rejected_before_connection(raw):
    with patch.object(remote.PinnedConnection, "connect") as connect, pytest.raises((ValueError, OSError)):
        remote.fetch(raw, MAX_BYTES)
    connect.assert_not_called()


def test_dns_mixed_results_rejected_and_public_ip_pinned():
    public = (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("93.184.216.34", 443))
    private = (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("127.0.0.1", 443))
    with patch.object(remote.socket, "getaddrinfo", return_value=[public, private]), pytest.raises(ValueError):
        remote.public_target("https://example.invalid/")
    with patch.object(remote.socket, "getaddrinfo", return_value=[public]):
        _, host, port, addr = remote.public_target("https://example.invalid/")
    with patch.object(remote.socket, "socket") as sock, patch.object(remote.ssl, "create_default_context") as tls:
        remote.PinnedConnection(host, port, addr, tls=True).connect()
        sock.return_value.connect.assert_called_once_with(("93.184.216.34", 443))
        tls.return_value.wrap_socket.assert_called_once_with(sock.return_value, server_hostname="example.invalid")


def response(body=b"image", status=200, **headers):
    result = Mock(status=status)
    result.getheader.side_effect = lambda key, default=None: headers.get(key, default)
    result.read.return_value = body
    return result


def test_redirect_to_private_address_is_rechecked():
    with (
        patch.object(remote, "public_target", wraps=remote.public_target) as check,
        patch.object(
            remote.socket,
            "getaddrinfo",
            side_effect=[
                [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))],
                [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 80))],
            ],
        ),
        patch.object(remote, "PinnedConnection") as connection,
    ):
        connection.return_value.getresponse.return_value = response(
            status=302, **{"Location": "http://127.0.0.1/secret"}
        )
        with pytest.raises(ValueError):
            remote.fetch("https://example.invalid/", MAX_BYTES)
        assert check.call_count == 2
        assert connection.call_count == 1


@pytest.mark.parametrize(
    "reply",
    [
        response(**{"Content-Length": str(MAX_BYTES + 1)}),
        response(body=b"x" * (MAX_BYTES + 1)),
        response(**{"Content-Encoding": "gzip"}),
        response(status=404),
        response(status=302),
    ],
)
def test_response_bounds(reply):
    with (
        patch.object(
            remote,
            "public_target",
            return_value=(remote.urlsplit("https://example.invalid/"), "example.invalid", 443, ()),
        ),
        patch.object(remote, "PinnedConnection") as connection,
    ):
        connection.return_value.getresponse.return_value = reply
        with pytest.raises(ValueError):
            remote.fetch("https://example.invalid/", MAX_BYTES)
        connection.return_value.close.assert_called()


def test_html_icon_discovery_and_fallback():
    target = (remote.urlsplit("https://example.invalid/profile"), "example.invalid", 443, ())
    with (
        patch.object(remote, "public_target", return_value=target),
        patch.object(
            remote,
            "fetch",
            side_effect=[
                (b'<link rel="shortcut icon" href="/brand.png">', "text/html", "https://example.invalid/profile"),
                (png(), "image/png", "https://example.invalid/brand.png"),
            ],
        ) as fetch,
    ):
        assert remote.download_favicon("https://example.invalid/profile") == png()
        assert fetch.call_args.args[0] == "https://example.invalid/brand.png"
    with (
        patch.object(remote, "public_target", return_value=target),
        patch.object(
            remote, "fetch", side_effect=[ValueError(), (png(), "image/x-icon", "https://example.invalid/favicon.ico")]
        ) as fetch,
    ):
        assert remote.download_favicon("https://example.invalid/profile") == png()
        assert fetch.call_args.args[0].endswith("/favicon.ico")


@pytest.mark.parametrize(
    "body", [b"<svg></svg>", b"<html>error</html>", b"", b"x" * (MAX_BYTES + 1), png((2100, 2000))]
)
def test_invalid_favicon_pixels(body):
    with pytest.raises(ValidationError):
        validated_favicon(body)


@pytest.mark.parametrize("fmt", ["PNG", "ICO", "JPEG", "WEBP", "GIF"])
def test_remote_raster_sanitized(fmt):
    file = validated_favicon(png(fmt=fmt) + b"trailing payload")
    with Image.open(file) as image:
        assert image.format == "PNG"
        assert image.width <= 128 and image.height <= 128
    assert b"trailing payload" not in file.read()


def test_relay_timeouts_and_errors():
    with patch("apps.jrc_channels.favicons.http.client.HTTPConnection") as conn:
        conn.return_value.getresponse.side_effect = TimeoutError
        with pytest.raises(ValidationError):
            fetch_favicon("https://example.invalid/")
        assert conn.call_args.kwargs["timeout"] == 10
        conn.return_value.close.assert_called_once()
    with pytest.raises(ValidationError):
        fetch_favicon("")


def test_refresh_preserves_manual_icon_and_stale_result(owner, workspace, local_storage):
    account = create_manual_channel(workspace, "custom", "Members", profile_url="https://example.invalid/")
    account.jrc_channel.icon = validated_favicon(png())
    account.jrc_channel.save()
    with patch("apps.jrc_channels.favicons.fetch_favicon", return_value=validated_favicon(png())):
        assert owner.post(url(workspace, account, "favicon")).status_code == 302
    account.refresh_from_db()
    profile = account.jrc_channel
    assert profile.favicon and profile.icon
    assert profile.effective_icon.name == profile.icon.name
    owner.post(url(workspace, account, "icon"), {"remove_icon": "1"})
    account.refresh_from_db()
    assert account.jrc_channel.effective_icon.name == profile.favicon.name
    assert not refresh_favicon(account.pk, "https://stale.invalid/")
    with patch("apps.jrc_channels.tasks.refresh_favicon", side_effect=ValidationError("offline")) as fetch:
        load_website_icon.now(str(account.pk), "https://example.invalid/")
        fetch.assert_called_once()


def test_link_change_clears_old_favicon_and_queues_new(owner, workspace, local_storage):
    account = create_manual_channel(workspace, "custom", "Members", profile_url="https://old.invalid/")
    account.jrc_channel.favicon = validated_favicon(png())
    account.jrc_channel.save()
    from django.test import TestCase

    with patch("apps.jrc_channels.tasks.load_website_icon") as queue, TestCase.captureOnCommitCallbacks(execute=True):
        owner.post(url(workspace, account, "edit"), {"name": "Members", "profile_url": "https://new.invalid/"})
    account.refresh_from_db()
    assert not account.jrc_channel.favicon
    queue.assert_called_once_with(str(account.pk), "https://new.invalid/")


@override_settings(JRC_FEATURES=MVP_FEATURES)
def test_colour_rendered_in_board_detail_publish_and_overview(owner, workspace, user):
    from django.utils import timezone

    from apps.composer.models import PlatformPost

    account = create_manual_channel(workspace, "instagram", "Long channel name " * 5)
    profile = account.jrc_channel
    profile.color = "#00FF00"
    profile.save()
    post = create_content(workspace, "Colour everywhere", user, social_account_ids=[account.pk])
    PlatformPost.objects.filter(post=post).update(status="scheduled", scheduled_at=timezone.now())
    for address in [
        reverse("jrc_production:board"),
        reverse("jrc_production:content_detail", kwargs={"workspace_id": workspace.pk, "post_id": post.pk}),
        reverse("calendar:calendar", kwargs={"workspace_id": workspace.pk}),
        reverse("calendar:calendar", kwargs={"workspace_id": workspace.pk}) + "?view=list",
    ]:
        response = owner.get(address)
        assert response.status_code == 200
        assert b"background:#00FF00;color:#000000" in response.content


def test_refresh_failure_keeps_icon_and_known_platform_is_rejected(owner, workspace, local_storage):
    account = create_manual_channel(workspace, "custom", "Members", profile_url="https://example.invalid/")
    account.jrc_channel.favicon = validated_favicon(png())
    account.jrc_channel.save()
    previous = account.jrc_channel.favicon.name
    with patch("apps.jrc_channels.favicons.fetch_favicon", side_effect=ValidationError("Unavailable")):
        assert owner.post(url(workspace, account, "favicon")).status_code == 302
    account.refresh_from_db()
    assert account.jrc_channel.favicon.name == previous
    known = create_manual_channel(workspace, "youtube", "YouTube")
    assert owner.post(url(workspace, known, "favicon")).status_code == 403


def test_stale_result_during_fetch_and_old_file_cleanup(workspace, local_storage, django_capture_on_commit_callbacks):
    from apps.jrc_channels.models import ChannelProfile

    account = create_manual_channel(workspace, "custom", "Members", profile_url="https://example.invalid/")
    profile = account.jrc_channel
    profile.favicon = validated_favicon(png())
    profile.save()
    previous, storage = profile.favicon.name, profile.favicon.storage
    with (
        patch("apps.jrc_channels.favicons.fetch_favicon", return_value=validated_favicon(png())),
        django_capture_on_commit_callbacks(execute=True),
    ):
        assert refresh_favicon(account.pk, profile.profile_url)
    assert not storage.exists(previous)
    account.refresh_from_db()
    latest = account.jrc_channel.favicon.name

    def race(_url):
        ChannelProfile.objects.filter(pk=account.pk).update(profile_url="https://changed.invalid/")
        return validated_favicon(png())

    with patch("apps.jrc_channels.favicons.fetch_favicon", side_effect=race):
        assert not refresh_favicon(account.pk, profile.profile_url)
    account.refresh_from_db()
    assert account.jrc_channel.favicon.name == latest


@override_settings(JRC_FEATURES=MVP_FEATURES)
@pytest.mark.parametrize("view", ["month", "week", "day", "list"])
def test_calendar_event_itself_uses_colour(owner, workspace, user, view):
    from html.parser import HTMLParser

    from django.utils import timezone

    class Tags(HTMLParser):
        def __init__(self):
            super().__init__()
            self.coloured = []

        def handle_starttag(self, tag, attrs):
            values = dict(attrs)
            if "background:#FFFF00;color:#000000" in values.get("style", ""):
                self.coloured.append(values)

    account = create_manual_channel(workspace, "instagram", "Calendar colour")
    profile = account.jrc_channel
    profile.color = "#FFFF00"
    profile.save()
    post = create_content(workspace, "Calendar colour", user, social_account_ids=[account.pk])
    variant = post.platform_posts.get()
    variant.status = "scheduled"
    variant.scheduled_at = timezone.now()
    variant.save()
    response = owner.get(
        reverse("calendar:calendar", kwargs={"workspace_id": workspace.pk}), {"mode": "calendar", "view": view}
    )
    assert response.status_code == 200
    tags = Tags()
    tags.feed(response.content.decode())
    if view == "list":
        assert any(
            "text-xs" in tag.get("class", "") and tag.get("class") != "sidebar-nav-item" for tag in tags.coloured
        )
    else:
        # Assert on event elements, so a coloured sidebar cannot hide a missed calendar partial.
        assert sum("post-chip" in tag.get("class", "") for tag in tags.coloured) >= (1 if view == "day" else 2)


def test_creation_only_queues_custom_profile(owner, workspace, django_capture_on_commit_callbacks, no_network):
    address = reverse("jrc_channels:profile", kwargs={"workspace_id": workspace.pk})
    for platform in ["custom", "instagram"]:
        with (
            patch("apps.jrc_channels.tasks.load_website_icon") as queue,
            django_capture_on_commit_callbacks(execute=True),
        ):
            response = owner.post(
                address, {"platform": platform, "name": "Synthetic", "profile_url": "https://example.invalid/profile"}
            )
        assert response.status_code == 302
        assert queue.call_count == (1 if platform == "custom" else 0)


def test_renaming_custom_channel_preserves_default_colour(owner, workspace):
    account = create_manual_channel(workspace, "custom", "Forendors", profile_url="https://forendors.cz/example")
    original = account.jrc_accent_color
    assert original == "#047857"
    assert owner.post(url(workspace, account, "edit"), {"name": "Membership", "profile_url": ""}).status_code == 302
    account.refresh_from_db()
    assert account.jrc_accent_color == original
    assert account.jrc_channel.color == original
