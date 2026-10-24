"""F5-B03: browser issuance, redirect safety, CSRF and coordinated logout."""

import hashlib
import uuid
from urllib.parse import parse_qs, urlsplit

import pytest
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
    assert "_auth_user_id" in browser.session
    assert browser.post("/accounts/logout/").status_code == 403
    response = browser.post(
        "/accounts/logout/?next=https://foreign.invalid",
        {"csrfmiddlewaretoken": browser.cookies["csrftoken"].value, "next": "//foreign.invalid"},
    )
    assert response.url == "/video/jrc-logout"
    assert "_auth_user_id" not in browser.session
    assert response.cookies["jrc_sso_binding"]["max-age"] == 0
