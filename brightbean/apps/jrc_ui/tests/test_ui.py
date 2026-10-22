# JR Content: přidáno 2026-10-23 (blok F2-B10), AGPL-3.0.
from unittest.mock import patch
from uuid import uuid4

import pytest
from django.urls import reverse

from apps.jrc_ui.features import MVP_FEATURES
from apps.members.models import WorkspaceMembership
from apps.workspaces.models import Workspace


@pytest.fixture(autouse=True)
def mvp_settings(settings):
    settings.JRC_FEATURES = MVP_FEATURES.copy()


@pytest.fixture
def workspace(organization, org_owner):
    workspace = Workspace.objects.create(organization=organization, name="Local content")
    WorkspaceMembership.objects.create(user=org_owner, workspace=workspace, workspace_role="owner")
    return workspace


BLOCKED_PATHS = [
    "/workspace/{id}/inbox/",
    "/workspace/{id}/analytics/",
    "/api/v1/inbox/threads",
    "/api/v1/analytics/summary",
    "/accounts/review-banner/dismiss/",
    "/onboarding/{id}/checklist/",
    "/onboarding/connect/example/",
    "/onboarding/connect/example/oauth/start/",
    "/workspace/{id}/create/",
    "/workspace/{id}/ideas/board/",
    "/workspace/{id}/ideas/create/",
    "/workspace/{id}/settings/clients/",
    "/portal/example/",
    "/intelligence/finalizing/",
    "/orgs/{id}/intelligence/playground/",
    "/social-accounts/{id}/connect/",
    "/social-accounts/{id}/connect/bluesky/",
    "/social-accounts/{id}/{id}/reconnect/",
    "/social-accounts/{id}/{id}/retry-webhooks/",
    "/social-accounts/callback/facebook/",
    "/social-accounts/select-account/",
    "/workspace/{id}/compose/unsplash-search/",
    "/workspace/{id}/compose/unsplash-import/",
    "/workspace/{id}/compose/{id}/unsplash-import/",
    "/accounts/google/login/",
    "/accounts/google/login/callback/",
    "/workspace/{id}/feeds/",
    "/workspace/{id}/feeds/add/",
    "/workspace/{id}/feeds/explore/",
]


@pytest.mark.parametrize("path", BLOCKED_PATHS)
@pytest.mark.parametrize("method", ["get", "post"])
def test_hidden_urls_never_execute_views(client, path, method):
    # I anonymní POST vrací 404 před ověřením přihlášení, CSRF a voláním view.
    with patch("httpx.get", side_effect=AssertionError("Unexpected network call")):
        response = getattr(client, method)(path.format(id=uuid4()))
    assert response.status_code == 404


@pytest.mark.django_db
@pytest.mark.parametrize(
    "view", ["calendar:calendar", "composer:compose", "media_library:index", "workspaces:settings"]
)
def test_main_pages_keep_content_tools_without_hidden_navigation(client, user, workspace, view):
    client.force_login(user)
    with patch("httpx.get", side_effect=AssertionError("Unexpected network call")):
        response = client.get(reverse(view, kwargs={"workspace_id": workspace.id}))
    assert response.status_code == 200
    html = response.content.decode()
    for forbidden in (
        "/inbox/",
        "/analytics/",
        "/ideas/",
        "/feeds/",
        "/connect/",
        "/settings/clients/",
        "onboarding-checklist",
        "trustpilot.com",
        'title="Search Unsplash"',
        "cdn.jsdelivr.net",
    ):
        assert forbidden not in html
    assert "JR Content" in html
    assert "/about/" in html


def test_disabled_rss_does_not_even_resolve_domain():
    from apps.composer.views import _safe_fetch_feed

    with patch("apps.composer.views.is_safe_url") as validate, patch("httpx.get") as fetch:
        assert _safe_fetch_feed("https://example.invalid/feed.xml", {}) == (None, None)
    validate.assert_not_called()
    fetch.assert_not_called()


def test_onboarding_does_not_query_when_disabled(rf):
    from apps.jrc_ui.context_processors import onboarding_checklist

    with patch("apps.onboarding.context_processors.onboarding_checklist") as checklist:
        assert onboarding_checklist(rf.get("/")) == {}
    checklist.assert_not_called()


@pytest.mark.django_db
def test_disabling_features_is_reversible(client, user, workspace, settings):
    client.force_login(user)
    path = reverse("composer:create_landing", kwargs={"workspace_id": workspace.id})
    assert client.get(path).status_code == 404
    settings.JRC_FEATURES = {**MVP_FEATURES, "ideas": True}
    assert client.get(path).status_code == 200


@pytest.mark.django_db
def test_local_about_and_license(client):
    response = client.get("/about/")
    assert response.status_code == 200
    assert b"github.com/brightbeanxyz/brightbean-studio" in response.content
    response = client.get("/about/license/")
    assert response.status_code == 200
    assert b"GNU AFFERO GENERAL PUBLIC LICENSE" in b"".join(response.streaming_content)


@pytest.mark.django_db
@pytest.mark.parametrize("path", ["/accounts/login/", "/accounts/signup/"])
def test_local_auth_has_no_external_signin(client, path):
    response = client.get(path)
    assert response.status_code == 200
    assert b"/accounts/google/" not in response.content


@pytest.mark.django_db
def test_connected_and_unhealthy_channels_do_not_restore_hidden_features(client, user, workspace):
    from apps.social_accounts.models import SocialAccount

    for status in ("connected", "error"):
        SocialAccount.objects.create(
            workspace=workspace,
            platform="facebook",
            account_platform_id=f"local-{status}",
            account_name=f"Local {status}",
            connection_status=status,
        )
    client.force_login(user)
    response = client.get(reverse("social_accounts:list", kwargs={"workspace_id": workspace.id}))
    assert response.status_code == 200
    html = response.content.decode()
    for forbidden in ("trustpilot.com", "/inbox/", "/analytics/", "/connect/", "/reconnect/", "/retry-webhooks/"):
        assert forbidden not in html
