# JR Content: přidáno 2026-11-04 (blok P1-B11), AGPL-3.0.
"""Bez obrazovky souhlasu s podmínkami brightbean.xyz („Almost there“) a bez odkazů na ně."""

from datetime import timedelta
from pathlib import Path

import pytest
from django.conf import settings as django_settings
from django.template.loaders.app_directories import get_app_template_dirs
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.jrc_ui.features import MVP_FEATURES
from apps.members.models import Invitation, OrgMembership
from apps.workspaces.models import Workspace

PASSWORD = "local-test-password-9431"


@pytest.fixture(autouse=True)
def mvp_settings(settings):
    settings.JRC_FEATURES = MVP_FEATURES.copy()


def _new_user(email="new-owner@example.invalid"):
    # Stejně jako createsuperuser/shell: bez tos_accepted_at.
    user = User.objects.create_user(email=email, password=PASSWORD, name="New Owner")
    assert user.tos_accepted_at is None
    return user


def _login(client, email):
    return client.post(reverse("account_login"), {"login": email, "password": PASSWORD}, follow=True)


def _visited(response):
    return [url for url, _status in response.redirect_chain]


def test_terms_is_off_in_mvp_and_wrapped_middleware_is_configured():
    assert MVP_FEATURES["terms"] is False
    assert "apps.accounts.middleware.TosAcceptanceMiddleware" not in django_settings.MIDDLEWARE
    assert "apps.jrc_ui.middleware.TermsAcceptanceMiddleware" in django_settings.MIDDLEWARE


@pytest.mark.django_db
def test_new_user_goes_straight_into_the_app(client):
    user = _new_user()
    response = _login(client, user.email)
    assert response.status_code == 200
    assert all("accept-terms" not in url for url in _visited(response))
    assert b"Almost there" not in response.content
    assert b"brightbean.xyz" not in response.content
    workspace = Workspace.objects.filter(organization__memberships__user=user).first()
    assert workspace is not None
    page = client.get(reverse("calendar:calendar", kwargs={"workspace_id": workspace.id}))
    assert page.status_code == 200
    user.refresh_from_db()
    assert user.tos_accepted_at is None


@pytest.mark.django_db
@pytest.mark.parametrize("method", ["get", "post"])
def test_terms_page_is_not_reachable(client, method):
    client.force_login(_new_user())
    response = getattr(client, method)("/accounts/accept-terms/", {"agree_tos": "on"})
    assert response.status_code == 404


@pytest.mark.django_db
def test_switching_terms_on_restores_upstream_redirect(client, settings):
    client.force_login(_new_user())
    settings.JRC_FEATURES = {**MVP_FEATURES, "terms": True}
    response = client.get("/")
    assert response.status_code == 302
    assert response["Location"] == reverse("accounts:accept_terms")


@pytest.mark.django_db
def test_invited_person_signs_up_without_brightbean_terms(client, settings, organization, org_owner):
    settings.SIGNUP_MODE = "invite"
    invitation = Invitation.objects.create(
        organization=organization,
        email="invited-client@example.invalid",
        org_role=OrgMembership.OrgRole.MEMBER,
        invited_by=org_owner,
        expires_at=timezone.now() + timedelta(days=7),
    )
    invite_page = client.get(reverse("members:accept_invite", kwargs={"token": invitation.token}))
    assert invite_page.status_code == 200
    assert b"brightbean.xyz" not in invite_page.content
    signup_page = client.get(reverse("account_signup"))
    assert signup_page.status_code == 200
    assert b"Create Account" in signup_page.content
    assert b"brightbean.xyz" not in signup_page.content
    assert b"Terms of Service" not in signup_page.content
    response = client.post(reverse("account_signup"), {"email": invitation.email, "password1": PASSWORD}, follow=True)
    assert response.status_code == 200
    assert all("accept-terms" not in url for url in _visited(response))
    user = User.objects.get(email=invitation.email)
    assert OrgMembership.objects.filter(user=user, organization=organization).exists()


@pytest.mark.django_db
def test_signup_keeps_upstream_terms_when_switched_on(client, settings):
    settings.JRC_FEATURES = {**MVP_FEATURES, "terms": True}
    response = client.get(reverse("account_signup"))
    assert b"https://brightbean.xyz/terms-of-service/" in response.content


# Šablony, které odkaz na brightbean.xyz obsahují, ale v jrc se nezobrazí:
# klíčem je cesta šablony, hodnotou funkce, která je v MVP vypnutá a stránku skrývá.
HIDDEN_BY_FEATURE = {
    "account/accept_terms.html": "terms",
    "account/signup.html": "terms",  # odstavec v {% if jrc_features.terms %}, ověřeno výše
    "partials/_review_banner.html": "reviews",
    "intelligence/_finalizing_failed.html": "intelligence",
    "intelligence/_finalizing_unauthorized.html": "intelligence",
    "intelligence/_overlay_provisioning_failed.html": "intelligence",
    "intelligence/activation_failed.html": "intelligence",
    "intelligence/activation_org_mismatch.html": "intelligence",
    "intelligence/deployment_not_authorized.html": "intelligence",
}


def test_no_brightbean_xyz_link_in_templates_shown_in_jrc():
    template_dirs = [Path(d) for d in django_settings.TEMPLATES[0]["DIRS"]]
    template_dirs += [Path(d) for d in get_app_template_dirs("templates")]
    found = set()
    for base in template_dirs:
        for path in base.rglob("*"):
            if (
                path.is_file()
                and path.suffix in {".html", ".txt"}
                and "brightbean.xyz" in path.read_text(encoding="utf-8", errors="replace")
            ):
                found.add(path.relative_to(base).as_posix())
    assert found, "šablony nenalezeny"
    assert found <= set(HIDDEN_BY_FEATURE), sorted(found - set(HIDDEN_BY_FEATURE))
    for template, feature in HIDDEN_BY_FEATURE.items():
        assert MVP_FEATURES[feature] is False, template
