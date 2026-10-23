# JR Content: upraveno 2026-10-23 (blok F2-B03), AGPL-3.0.
"""Routing tests for user-uploaded media (``MEDIA_URL`` -> ``MEDIA_ROOT``).

Three separate regressions are pinned here.

The first is issue #130: ``config/urls.py`` used to mount media only under
``settings.DEBUG``, so a ``config.settings.production`` deployment with
``STORAGE_BACKEND=local`` returned 404 for every upload. That breaks more than
the thumbnails in the UI — ``apps/publisher/engine.py`` builds each attachment's
outbound URL as ``APP_URL + asset.file.url``, and Instagram, Threads, Facebook,
Pinterest, Google Business and dev.to fetch that URL server-side with no
byte-upload fallback, so publishing to them fails too.

The second is the shape of the route. ``MEDIA_URL`` is only assigned on the
local-storage branch of ``config/settings/base.py``; under ``STORAGE_BACKEND=s3``
Django normalises the unset default to ``"/"``, and a ``"/"`` prefix compiles to
``^(?P<path>.*)$`` — a catch-all appended after every real route, serving
whatever it matches out of the process CWD. That is how the dev server came to
answer ``GET /.env`` with the repo's own environment file.

The third is the middleware stack in front of the route: avatars and workspace
icons must remain accessible on the accept-terms page. JR Content adds a fourth
regression: library originals, thumbnails and historical versions require login
and membership of their owning workspace (or org for shared assets). Public
page chrome must never provide a traversal bypass into that library.
"""

import importlib
import importlib.util
import logging
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

import pytest
from django.conf import settings
from django.test import Client, override_settings
from django.urls import Resolver404, clear_url_caches, resolve
from django.utils import timezone

import config.urls
from apps.accounts.models import User
from apps.media_library.file_views import private_media
from apps.media_library.models import MediaAsset, MediaAssetVersion
from apps.members.models import OrgMembership, WorkspaceMembership
from apps.organizations.models import Organization
from apps.workspaces.models import Workspace
from config.urls import PUBLIC_MEDIA_PREFIXES, media_urlpatterns

BASE_SETTINGS_PATH = Path(config.urls.__file__).resolve().parent / "settings" / "base.py"


@contextmanager
def urlconf_built_with(**overrides):
    """Rebuild ``config.urls`` under ``overrides``, then restore the real one.

    ``override_settings`` on its own changes nothing here: the route is
    appended when the module first executes, so both ``SERVE_MEDIA`` and the
    ``document_root`` baked into the pattern are fixed at import time.
    """
    try:
        with override_settings(**overrides):
            importlib.reload(config.urls)
            clear_url_caches()
            yield
    finally:
        importlib.reload(config.urls)
        clear_url_caches()


def _base_settings_with_env(**environ):
    """Execute ``config/settings/base.py`` under ``environ`` as a throwaway module.

    Loaded under a private name so the real ``config.settings.base`` is left
    alone. ``base.py`` reads ``.env`` with ``overwrite=False``, so the values
    passed here win on a developer machine as well as in CI.
    """
    spec = importlib.util.spec_from_file_location("config.settings._probe", BASE_SETTINGS_PATH)
    module = importlib.util.module_from_spec(spec)
    with mock.patch.dict(os.environ, environ):
        spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# The SERVE_MEDIA setting itself
# ---------------------------------------------------------------------------


def test_s3_backend_forces_serve_media_off():
    """Object storage hands out its own presigned URLs; this process serves nothing."""
    assert _base_settings_with_env(STORAGE_BACKEND="s3").SERVE_MEDIA is False


def test_s3_backend_ignores_an_explicit_serve_media():
    """The env var is documented as having no effect on the s3 branch."""
    assert _base_settings_with_env(STORAGE_BACKEND="s3", SERVE_MEDIA="true").SERVE_MEDIA is False


def test_local_backend_serves_media_by_default():
    base = _base_settings_with_env(STORAGE_BACKEND="local")

    assert base.SERVE_MEDIA is True
    assert base.MEDIA_URL == "/media/"


def test_local_backend_honours_serve_media_false():
    """The opt-out for deployments whose proxy or CDN serves MEDIA_ROOT instead."""
    assert _base_settings_with_env(STORAGE_BACKEND="local", SERVE_MEDIA="false").SERVE_MEDIA is False


# ---------------------------------------------------------------------------
# The helper that builds the route
# ---------------------------------------------------------------------------


def test_helper_builds_one_prefixed_route():
    with tempfile.TemporaryDirectory() as media_root:
        with override_settings(MEDIA_URL="/media/", MEDIA_ROOT=media_root):
            patterns = media_urlpatterns()

        assert len(patterns) == len(PUBLIC_MEDIA_PREFIXES) + 1
        assert {p.callback for p in patterns} == {config.urls.public_media, private_media}
        assert {p.default_args["document_root"] for p in patterns} == {media_root}


@pytest.mark.parametrize("media_url", ["", "/"])
def test_helper_refuses_to_build_a_catch_all(media_url):
    """An empty prefix would match every unrouted URL — build nothing instead."""
    with (
        tempfile.TemporaryDirectory() as media_root,
        override_settings(MEDIA_URL=media_url, MEDIA_ROOT=media_root),
    ):
        assert media_urlpatterns() == []


def test_helper_ignores_an_absolute_media_url():
    """A CDN MEDIA_URL has nothing local to route; a pattern from it never matches."""
    with (
        tempfile.TemporaryDirectory() as media_root,
        override_settings(MEDIA_URL="https://cdn.example.com/media/", MEDIA_ROOT=media_root),
    ):
        assert media_urlpatterns() == []


def test_helper_warns_when_document_root_is_missing(caplog):
    """Without MEDIA_ROOT the route would serve relative to the process CWD."""
    with (
        override_settings(MEDIA_URL="/media/", MEDIA_ROOT=""),
        caplog.at_level(logging.WARNING, logger="config.urls"),
    ):
        assert media_urlpatterns() == []

    assert "MEDIA_ROOT is empty" in caplog.text


# ---------------------------------------------------------------------------
# The URLconf as actually built
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "/media/media_library/2026/09/example.png",
        "/media/media_library/thumbs/2026/09/example.jpg",
        "/media/media_library/versions/2026/09/example.jpg",
        "/media/avatars/2026/09/me.png",
        "/media/workspaces/icons/2026/09/logo.png",
    ],
)
def test_media_prefixes_are_routed_with_debug_off(path):
    """The regression from #130: DEBUG is False here, the route still exists."""
    assert settings.DEBUG is False

    match = resolve(path)

    assert match.func is (private_media if path.startswith("/media/media_library/") else config.urls.public_media)
    assert match.kwargs["path"] == path.removeprefix("/media/")


@pytest.mark.parametrize(
    "path",
    [
        "/media/comment_attachments/2026/09/private.png",
        "/media/csv_imports/leads.csv",
        "/media/some_future_prefix/thing.bin",
    ],
)
def test_non_public_prefixes_are_not_routed(path):
    """PUBLIC_MEDIA_PREFIXES is an allowlist, so anything unlisted has no route.

    comment_attachments/ is the one that matters today: PostComment.visibility
    can be "internal", which apps/client_portal/views.py filters out of what a
    portal client is shown, so a public URL would leak exactly that.
    """
    with pytest.raises(Resolver404):
        resolve(path)


def test_serve_media_off_removes_the_route():
    with urlconf_built_with(SERVE_MEDIA=False), pytest.raises(Resolver404):
        resolve("/media/media_library/2026/09/example.png")


@pytest.mark.parametrize("path", ["/.env", "/requirements.txt", "/config/settings/base.py"])
def test_urlconf_has_no_catch_all(path):
    """Nothing outside /media/ may fall through to the static file server."""
    with pytest.raises(Resolver404):
        resolve(path)


def test_library_file_requires_login():
    with tempfile.TemporaryDirectory() as media_root:
        probe = Path(media_root) / "media_library" / "2026" / "09" / "routing-probe.png"
        probe.parent.mkdir(parents=True)
        probe.write_bytes(b"probe-bytes")

        with urlconf_built_with(SERVE_MEDIA=True, MEDIA_URL="/media/", MEDIA_ROOT=media_root):
            response = Client().get("/media/media_library/2026/09/routing-probe.png")

            assert response.status_code == 302
            assert response["Location"].startswith("/accounts/login/?next=")
            assert "no-store" in response["Cache-Control"]


def test_a_file_outside_the_public_prefixes_is_not_served():
    """Present on disk, reachable by no URL — the comment_attachments/ case."""
    with tempfile.TemporaryDirectory() as media_root:
        probe = Path(media_root) / "comment_attachments" / "2026" / "09" / "private.png"
        probe.parent.mkdir(parents=True)
        probe.write_bytes(b"internal-only")

        with urlconf_built_with(SERVE_MEDIA=True, MEDIA_URL="/media/", MEDIA_ROOT=media_root):
            response = Client().get("/media/comment_attachments/2026/09/private.png")

            assert response.status_code == 404


@pytest.mark.django_db
def test_media_is_exempt_from_the_tos_redirect():
    """A user who hasn't accepted the ToS still gets the bytes, not a 302.

    Every account created by ``manage.py createsuperuser`` has a NULL
    ``tos_accepted_at``, and the accept-terms page itself renders avatars and
    workspace icons out of MEDIA_ROOT.
    """
    user = User.objects.create_user(email="no-tos@example.com", password="testpass123")
    assert user.tos_accepted_at is None

    with tempfile.TemporaryDirectory() as media_root:
        probe = Path(media_root) / "avatars" / "2026" / "09" / "avatar.png"
        probe.parent.mkdir(parents=True)
        probe.write_bytes(b"png-bytes")

        with urlconf_built_with(SERVE_MEDIA=True, MEDIA_URL="/media/", MEDIA_ROOT=media_root):
            client = Client()
            client.force_login(user)

            response = client.get("/media/avatars/2026/09/avatar.png")

            assert response.status_code == 200
            assert b"".join(response.streaming_content) == b"png-bytes"


@pytest.mark.django_db
def test_tos_redirect_still_applies_off_the_media_prefix():
    """The exemption is scoped to /media/ — it must not disarm the middleware."""
    user = User.objects.create_user(email="also-no-tos@example.com", password="testpass123")
    client = Client()
    client.force_login(user)

    response = client.get("/")

    assert response.status_code == 302
    assert response["Location"] == "/accounts/accept-terms/"

    user.tos_accepted_at = timezone.now()
    user.save(update_fields=["tos_accepted_at"])

    # The dashboard forwards to the user's workspace, so assert on where it
    # does *not* send them rather than on a bare 200.
    assert client.get("/").get("Location", "") != "/accounts/accept-terms/"


@pytest.fixture
def private_library(tmp_path):
    """Real disk files, including historical thumbnails, with explicit owners."""
    with urlconf_built_with(SERVE_MEDIA=True, MEDIA_URL="/media/", MEDIA_ROOT=tmp_path):
        organization = Organization.objects.create(name="Private org")
        workspace = Workspace.objects.create(organization=organization, name="Client A")
        other_workspace = Workspace.objects.create(organization=organization, name="Client B")
        member = User.objects.create_user(email="member@example.invalid", tos_accepted_at=timezone.now())
        outsider = User.objects.create_user(email="outsider@example.invalid", tos_accepted_at=timezone.now())
        for user in (member, outsider):
            OrgMembership.objects.create(user=user, organization=organization)
        WorkspaceMembership.objects.create(user=member, workspace=workspace)
        WorkspaceMembership.objects.create(user=outsider, workspace=other_workspace)
        asset = MediaAsset.objects.create(
            organization=organization,
            workspace=workspace,
            file="media_library/2026/10/original.png",
            thumbnail="media_library/thumbs/2026/10/current.jpg",
            filename="original.png",
            media_type="image",
        )
        version = MediaAssetVersion.objects.create(
            media_asset=asset,
            version_number=1,
            file="media_library/versions/2026/10/old.png",
            thumbnail="media_library/thumbs/2026/10/old.jpg",
        )
        paths = [asset.file.name, asset.thumbnail.name, version.file.name, version.thumbnail.name]
        for path in paths:
            target = tmp_path / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"private-client-bytes")
        yield asset, member, outsider, paths, tmp_path


@pytest.mark.django_db
@pytest.mark.parametrize("identity", ["anonymous", "other_workspace", "member"])
@pytest.mark.parametrize("file_index", range(4), ids=["original", "thumbnail", "version", "version_thumbnail"])
def test_library_file_membership(private_library, identity, file_index):
    _, member, outsider, paths, _ = private_library
    client = Client()
    if identity != "anonymous":
        client.force_login(member if identity == "member" else outsider)
    response = client.get("/media/" + paths[file_index])
    assert response.status_code == {"anonymous": 302, "other_workspace": 404, "member": 200}[identity]
    if identity == "member":
        assert b"".join(response.streaming_content) == b"private-client-bytes"
        assert "private" in response["Cache-Control"]
        assert "no-store" in response["Cache-Control"]


@pytest.mark.django_db
def test_library_access_is_checked_again_after_membership_revocation(private_library):
    _, member, _, paths, _ = private_library
    client = Client()
    client.force_login(member)
    response = client.head("/media/" + paths[0])
    assert response.status_code == 200
    assert b"".join(response.streaming_content) == b""
    WorkspaceMembership.objects.filter(user=member).delete()
    assert client.get("/media/" + paths[0]).status_code == 404
    client.logout()
    assert client.get("/media/" + paths[0]).status_code == 302


@pytest.mark.django_db
def test_shared_library_requires_membership_of_the_owning_org(private_library):
    asset, member, _, paths, _ = private_library
    asset.workspace = None
    asset.save(update_fields=["workspace"])
    stranger = User.objects.create_user(email="other-org@example.invalid", tos_accepted_at=timezone.now())
    other_org = Organization.objects.create(name="Other org")
    OrgMembership.objects.create(user=stranger, organization=other_org)
    client = Client()
    client.force_login(stranger)
    assert client.get("/media/" + paths[0]).status_code == 404
    client.force_login(member)
    response = client.get("/media/" + paths[0])
    assert response.status_code == 200
    response.close()


@pytest.mark.django_db
def test_unregistered_file_is_not_served_even_to_a_member(private_library):
    _, member, _, _, media_root = private_library
    (media_root / "media_library/orphan.txt").write_text("orphan")
    client = Client()
    client.force_login(member)
    assert client.get("/media/media_library/orphan.txt").status_code == 404


@pytest.mark.django_db
@pytest.mark.parametrize("prefix", ["avatars/..", "workspaces/icons/../..", "avatars/%2e%2e"])
def test_public_route_cannot_traverse_into_private_library(private_library, prefix):
    _, _, _, paths, _ = private_library
    response = Client().get(f"/media/{prefix}/{paths[0]}")
    assert response.status_code == 404
