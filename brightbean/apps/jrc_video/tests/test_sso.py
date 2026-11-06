"""F5-B03: browser issuance, redirect safety, CSRF and coordinated logout."""

import hashlib
import uuid
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


def test_logout_requires_csrf_and_always_leads_through_freeframe(browser):
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
