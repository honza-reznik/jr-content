"""F5-B03: browser issuance, redirect safety, CSRF and coordinated logout."""

import hashlib
import uuid
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.utils import timezone

from apps.jrc_video.models import VideoAccount
from apps.jrc_video.tickets import issue_ticket
from apps.members.models import WorkspaceMembership
from apps.workspaces.models import Workspace

from .test_tickets import decode

pytestmark = pytest.mark.django_db


@pytest.fixture
def workspace(organization):
    return Workspace.objects.create(organization=organization, name="SSO test client")


@pytest.fixture
def browser(user, workspace):
    WorkspaceMembership.objects.get_or_create(user=user, workspace=workspace)
    VideoAccount.objects.create(user=user, ff_user_id=uuid.uuid4(), synced_at=timezone.now())
    client = Client(enforce_csrf_checks=True)
    client.force_login(user)
    return client


def test_anonymous_returns_via_brightbean_login():
    client = Client()
    response = client.get("/video-sso/?next=/projects/p")
    assert response.status_code == 302
    url = urlsplit(response.url)
    assert url.path == "/accounts/login/"
    assert parse_qs(url.query)["next"] == ["/video-sso/?next=/projects/p"]
    login = client.get(response.url)
    assert b'name="next" value="/video-sso/?next=/projects/p"' in login.content


def test_get_does_not_issue_and_post_is_csrf_protected(browser):
    response = browser.get("/video-sso/?next=/projects/p")
    assert response.status_code == 200
    assert b"Continue to video" in response.content
    assert "jrc_sso_binding" not in response.cookies
    assert browser.post("/video-sso/", {"next": "/projects/p"}).status_code == 403
    token = browser.cookies["csrftoken"].value
    assert (
        browser.post(
            "/video-sso/", {"next": "/projects/p", "csrfmiddlewaretoken": token}, HTTP_ORIGIN="https://foreign.invalid"
        ).status_code
        == 403
    )


def test_post_issues_only_fragment_and_sets_browser_binding(browser):
    browser.get("/video-sso/")
    response = browser.post(
        "/video-sso/", {"next": "/projects/p?embed=1", "csrfmiddlewaretoken": browser.cookies["csrftoken"].value}
    )
    assert response.status_code == 302
    location = urlsplit(response.url)
    assert location.path == "/video/jrc-sso" and not location.query
    ticket = parse_qs(location.fragment)["ticket"][0]
    assert decode(ticket)["next"] == "/projects/p?embed=1"
    cookie = response.cookies["jrc_sso_binding"]
    assert cookie.value == hashlib.sha256(ticket.encode()).hexdigest()
    assert cookie["samesite"] == "Strict" and cookie["max-age"] == 60 and cookie["path"] == "/video"
    assert ticket not in response.content.decode()
    assert "no-store" in response["Cache-Control"]
    assert response["Referrer-Policy"] == "no-referrer"


@pytest.mark.parametrize(
    "next_path",
    [
        "//foreign.invalid",
        "https://foreign.invalid",
        "/\\foreign",
        "/%2fevil",
        "/%252fevil",
        "/%5cevil",
        "/../accounts",
        "/a/../../evil",
        "/a/./b",
        "/a//b",
        "/%2e%2e/evil",
        "/projects#ticket=x",
        "/projects?next=%2f%2fevil",
        "/login?from=https://foreign.invalid",
        "/login?from=//foreign.invalid",
        "/setup",
        "/jrc-logout",
        "/jrc-sso",
        "/share/token",
        "/projects-evil",
        "/a\nb",
        "",
        "/" + "x" * 512,
    ],
)
def test_unsafe_next_refused_in_get_post_and_ticket_issuer(browser, user, next_path):
    assert browser.get("/video-sso/", {"next": next_path}).status_code == 400
    browser.get("/video-sso/")
    assert (
        browser.post(
            "/video-sso/", {"next": next_path, "csrfmiddlewaretoken": browser.cookies["csrftoken"].value}
        ).status_code
        == 400
    )
    with pytest.raises(ValueError):
        issue_ticket(user, audience="web", next_path=next_path)


def test_removed_member_cannot_issue(browser, user):
    WorkspaceMembership.objects.filter(user=user).delete()
    assert browser.get("/video-sso/").status_code == 403


def test_missing_account_shows_generic_error(browser, user):
    browser.get("/video-sso/")
    VideoAccount.objects.filter(user=user).delete()
    response = browser.post("/video-sso/", {"csrfmiddlewaretoken": browser.cookies["csrftoken"].value})
    assert response.status_code == 503
    assert b"Video is temporarily unavailable" in response.content
    assert b"TicketError" not in response.content


def test_logout_requires_csrf_and_always_leads_through_freeframe(browser, monkeypatch):
    video_client = Mock()
    monkeypatch.setattr("apps.jrc_video.views.FreeFrameUserClient", lambda user: video_client)
    response = browser.get("/accounts/logout/?next=https://foreign.invalid")
    assert response.status_code == 200
    assert "jrc_logout_binding" not in response.cookies
    assert "_auth_user_id" in browser.session
    assert browser.post("/accounts/logout/").status_code == 403
    assert (
        browser.post(
            "/accounts/logout/",
            {"csrfmiddlewaretoken": browser.cookies["csrftoken"].value},
            HTTP_ORIGIN="https://foreign.invalid",
        ).status_code
        == 403
    )
    video_client.request.assert_not_called()
    response = browser.post(
        "/accounts/logout/?next=https://foreign.invalid",
        {"csrfmiddlewaretoken": browser.cookies["csrftoken"].value, "next": "//foreign.invalid"},
    )
    location = urlsplit(response.url)
    assert location.path == "/video/jrc-logout" and not location.query
    grant = parse_qs(location.fragment)["grant"][0]
    cookie = response.cookies["jrc_logout_binding"]
    assert len(grant) == 43
    assert cookie.value == hashlib.sha256(grant.encode()).hexdigest()
    assert cookie["path"] == "/video" and cookie["samesite"] == "Strict" and cookie["max-age"] == 60
    assert "no-store" in response["Cache-Control"]
    assert response["Referrer-Policy"] == "no-referrer"
    assert "_auth_user_id" not in browser.session
    assert response.cookies["jrc_sso_binding"]["max-age"] == 0
    video_client.request.assert_called_once_with("POST", "/auth/jrc-logout")
    video_client.forget.assert_called_once()
    for name in ("ff_access_token", "ff_refresh_token"):
        assert response.cookies[name]["max-age"] == 0
        assert response.cookies[name]["path"] == "/video"


def _sign_out(browser, monkeypatch, side_effect):
    from apps.jrc_video.client import FreeFrameError  # noqa: F401 - used by callers' side effects

    video_client = Mock()
    video_client.request.side_effect = side_effect
    monkeypatch.setattr("apps.jrc_video.views.FreeFrameUserClient", lambda user: video_client)
    browser.get("/accounts/logout/")
    response = browser.post("/accounts/logout/", {"csrfmiddlewaretoken": browser.cookies["csrftoken"].value})
    return response, video_client


@pytest.mark.parametrize("status_code", [401, 403])
def test_logout_completes_without_queue_when_freeframe_refuses_the_account(browser, user, monkeypatch, status_code):
    """Deactivated, deleted, reset or unmanaged FreeFrame account: no tokens to revoke, nothing to retry."""
    from apps.jrc_video.client import FreeFrameError
    from apps.jrc_video.models import VideoSessionRevocation

    response, video_client = _sign_out(browser, monkeypatch, FreeFrameError("Refused", status_code=status_code))
    assert response.status_code == 302 and urlsplit(response.url).path == "/video/jrc-logout"
    assert "_auth_user_id" not in browser.session
    assert not VideoSessionRevocation.objects.exists()
    video_client.forget.assert_called_once()


@pytest.mark.parametrize(
    "failure",
    [
        pytest.param("outage", id="network"),
        pytest.param(503, id="redis-or-api-503"),
        pytest.param(500, id="api-500"),
        pytest.param("ticket", id="missing-sso-key"),
        pytest.param("boom", id="unexpected"),
    ],
)
def test_logout_completes_and_queues_revocation_when_freeframe_is_down(browser, user, monkeypatch, failure):
    from apps.jrc_video.client import FreeFrameError
    from apps.jrc_video.models import VideoSessionRevocation

    error = {
        "outage": FreeFrameError("FreeFrame API is unavailable"),
        503: FreeFrameError("HTTP 503", status_code=503),
        500: FreeFrameError("HTTP 500", status_code=500),
        "ticket": FreeFrameError("SSO key is not configured"),
        "boom": RuntimeError("unexpected"),
    }[failure]
    response, video_client = _sign_out(browser, monkeypatch, error)
    assert response.status_code == 302 and urlsplit(response.url).path == "/video/jrc-logout"
    assert "_auth_user_id" not in browser.session
    for name in ("ff_access_token", "ff_refresh_token"):
        assert response.cookies[name]["max-age"] == 0
    pending = VideoSessionRevocation.objects.get()
    assert pending.source_user_id == user.pk
    assert "password" not in pending.last_error.lower()
    video_client.forget.assert_called_once()


def test_logout_without_failure_queues_nothing(browser, monkeypatch):
    from apps.jrc_video.models import VideoSessionRevocation

    response, _ = _sign_out(browser, monkeypatch, None)
    assert response.status_code == 302
    assert not VideoSessionRevocation.objects.exists()


def test_logout_with_real_client_and_dead_freeframe_still_signs_out(browser, user, settings):
    """No mock of the client: connection refused at the real API address."""
    from apps.jrc_video.models import VideoSessionRevocation

    settings.JRC_FF_API_URL = "http://127.0.0.1:9"
    browser.get("/accounts/logout/")
    response = browser.post("/accounts/logout/", {"csrfmiddlewaretoken": browser.cookies["csrftoken"].value})
    assert response.status_code == 302
    assert "_auth_user_id" not in browser.session
    assert VideoSessionRevocation.objects.filter(source_user_id=user.pk).exists()


class TestQueuedSessionRevocation:
    """The worker finishes what the sign-out could not (jrc_sync_freeframe --pending)."""

    @pytest.fixture(autouse=True)
    def sync_settings(self, settings):
        settings.JRC_FF_SYNC_EMAIL = "sync@example.test"
        settings.JRC_FF_SYNC_PASSWORD = "unused-password"
        from apps.jrc_video.client import _TOKENS

        _TOKENS.clear()

    def test_retry_calls_service_endpoint_and_clears_marker(self, user):
        from django.core.management import call_command

        from apps.jrc_video.models import VideoSessionRevocation

        VideoSessionRevocation.objects.create(source_user_id=user.pk)
        calls = []

        def fake(self, method, path, payload=None):
            calls.append((method, path))
            return {"id": str(uuid.uuid4()), "found": True}

        with patch("apps.jrc_video.client.FreeFrameClient.request", fake):
            call_command("jrc_sync_freeframe", pending=True)
        assert ("POST", f"/jrc-sync/accounts/{user.pk}/revoke-sessions") in calls
        assert not VideoSessionRevocation.objects.exists()

    def test_failed_retry_keeps_marker_and_reports(self, user):
        from django.core.management import call_command
        from django.core.management.base import CommandError

        from apps.jrc_video.client import FreeFrameError
        from apps.jrc_video.models import VideoSessionRevocation

        VideoSessionRevocation.objects.create(source_user_id=user.pk)
        with (
            patch("apps.jrc_video.client.FreeFrameClient.request", side_effect=FreeFrameError("still down")),
            pytest.raises(CommandError),
        ):
            call_command("jrc_sync_freeframe", pending=True)
        assert VideoSessionRevocation.objects.get().last_error == "still down"

    def test_newer_sign_out_during_retry_is_not_lost(self, user):
        from apps.jrc_video.models import VideoSessionRevocation
        from apps.jrc_video.services import revoke_session_request

        pending = VideoSessionRevocation.objects.create(source_user_id=user.pk)

        class Api:
            def request(self, method, path, payload=None):
                # A second sign-out lands while the first call is in flight.
                VideoSessionRevocation.objects.filter(pk=pending.pk).update(requested_at=timezone.now())

        revoke_session_request(pending, Api())
        assert VideoSessionRevocation.objects.filter(pk=pending.pk).exists()


def test_logout_without_video_account_needs_no_freeframe(browser, user, monkeypatch):
    constructor = Mock()
    monkeypatch.setattr("apps.jrc_video.views.FreeFrameUserClient", constructor)
    VideoAccount.objects.filter(user=user).delete()
    browser.get("/accounts/logout/")
    response = browser.post("/accounts/logout/", {"csrfmiddlewaretoken": browser.cookies["csrftoken"].value})
    assert response.status_code == 302
    constructor.assert_not_called()


# ---- P1-B30 (E-13b-ex10-04): another client's video gets the same clear refusal as no client at all ----------

NO_ACCESS = b"You do not have access to video."


@pytest.fixture
def other_client(organization):
    """Another client of the same organization with its synchronized project and one channel video."""
    from apps.jrc_channels.services import create_manual_channel
    from apps.jrc_production.services import create_content
    from apps.jrc_video.models import VideoAsset, VideoProject

    other = Workspace.objects.create(organization=organization, name="Another client")
    owner = get_user_model().objects.create_user(
        email="other-owner@example.test", password="x-unused-1", name="Other", tos_accepted_at=timezone.now()
    )
    WorkspaceMembership.objects.create(user=owner, workspace=other, workspace_role="owner")
    create_manual_channel(other, "instagram", "Other Instagram")
    post = create_content(
        other, "Other video", owner, social_account_ids=other.social_accounts.values_list("pk", flat=True)
    )
    project = VideoProject.objects.create(workspace=other, ff_project_id=uuid.uuid4(), synced_at=timezone.now())
    asset = VideoAsset.objects.create(platform_post=post.platform_posts.get(), ff_asset_id=uuid.uuid4())
    return {"workspace": other, "project": project.ff_project_id, "asset": asset.ff_asset_id}


@pytest.fixture
def own_project(workspace):
    from apps.jrc_video.models import VideoProject

    return VideoProject.objects.create(workspace=workspace, ff_project_id=uuid.uuid4(), synced_at=timezone.now())


def sso_post(browser, next_path):
    browser.get("/video-sso/")
    return browser.post("/video-sso/", {"next": next_path, "csrfmiddlewaretoken": browser.cookies["csrftoken"].value})


@pytest.mark.parametrize(
    "template",
    [
        "/projects/{project}",
        "/projects/{project}/assets/{asset}?embed=1&version=1",
        "/projects/{project}/assets/{asset}",
        "/projects/{own}/assets/{asset}?embed=1",
        "/assets/{asset}",
        "/projects/{project}/settings",
    ],
)
def test_other_clients_video_is_refused_before_sign_in_with_the_clear_message(
    browser, other_client, own_project, template
):
    next_path = template.format(
        project=other_client["project"], asset=other_client["asset"], own=own_project.ff_project_id
    )

    for response in (browser.get("/video-sso/", {"next": next_path}), sso_post(browser, next_path)):
        assert response.status_code == 403
        assert NO_ACCESS in response.content
        assert "jrc_sso_binding" not in response.cookies
        assert "#ticket=" not in response.get("Location", "")


def test_no_client_at_all_gets_the_same_page(browser, user):
    WorkspaceMembership.objects.filter(user=user).delete()
    for response in (browser.get("/video-sso/", {"next": "/projects"}), sso_post(browser, "/projects")):
        assert response.status_code == 403
        assert NO_ACCESS in response.content and b"Back to JR Content" in response.content


@pytest.mark.parametrize(
    "template",
    [
        "/projects",
        "/projects/{own}",
        "/projects/{own}/assets/{own_asset}?embed=1&version=1",
        "/projects/{unknown}/assets/{unknown}",
        "/notifications",
    ],
)
def test_own_client_and_unknown_ids_still_sign_in(browser, user, workspace, own_project, template):
    from apps.jrc_channels.services import create_manual_channel
    from apps.jrc_production.services import create_content
    from apps.jrc_video.models import VideoAsset

    WorkspaceMembership.objects.filter(workspace=workspace, user=user).update(workspace_role="owner")
    create_manual_channel(workspace, "instagram", "Own Instagram")
    post = create_content(
        workspace, "Own video", user, social_account_ids=workspace.social_accounts.values_list("pk", flat=True)
    )
    own_asset = VideoAsset.objects.create(platform_post=post.platform_posts.get(), ff_asset_id=uuid.uuid4())
    next_path = template.format(own=own_project.ff_project_id, own_asset=own_asset.ff_asset_id, unknown=uuid.uuid4())

    response = sso_post(browser, next_path)

    assert response.status_code == 302
    assert decode(parse_qs(urlsplit(response.url).fragment)["ticket"][0])["next"] == next_path


@pytest.mark.parametrize("change", ["removed", "archived"])
def test_access_ends_with_the_membership_of_that_client(browser, user, workspace, own_project, other_client, change):
    WorkspaceMembership.objects.create(user=user, workspace=other_client["workspace"], workspace_role="client")
    next_path = f"/projects/{other_client['project']}/assets/{other_client['asset']}?embed=1"
    assert sso_post(browser, next_path).status_code == 302

    if change == "removed":
        WorkspaceMembership.objects.filter(user=user, workspace=other_client["workspace"]).delete()
    else:
        Workspace.objects.filter(pk=other_client["workspace"].pk).update(is_archived=True)

    response = sso_post(browser, next_path)
    assert response.status_code == 403 and NO_ACCESS in response.content
    # The own client is not affected.
    assert sso_post(browser, f"/projects/{own_project.ff_project_id}").status_code == 302
