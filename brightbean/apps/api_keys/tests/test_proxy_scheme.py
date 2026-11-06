"""P1-B29: Bearer keys work behind the TLS-terminating proxy and the "https" cannot be forged.

Chain in production: Tailscale Funnel/Serve (https) -> Caddy (docker/Caddyfile,
http) -> Brightbean. The scheme reaches Django as ``X-Forwarded-Proto``;
``TrustedProxyMiddleware`` keeps it only from the proxy container and
``SECURE_PROXY_SSL_HEADER`` turns it into ``request.is_secure()``.
"""

import pytest
from django.test import Client, override_settings

from apps.api_keys import services
from apps.members.models import OrgMembership, WorkspaceMembership
from apps.organizations.models import Organization
from apps.social_accounts.models import SocialAccount
from apps.workspaces.models import Workspace

pytestmark = pytest.mark.django_db
PROXY_IP = "172.30.0.2"
OUTSIDE_IP = "172.30.0.99"


@pytest.fixture
def token(db):
    from django.core.cache import cache

    from apps.accounts.models import User

    cache.clear()
    user = User.objects.create_user(email="scheme@example.com", password="x-testpass123", name="S")
    org = Organization.objects.create(name="Scheme org")
    OrgMembership.objects.create(user=user, organization=org, org_role="owner")
    ws = Workspace.objects.create(organization=org, name="Scheme ws")
    WorkspaceMembership.objects.create(user=user, workspace=ws, workspace_role="owner")
    sa = SocialAccount.objects.create(
        workspace=ws,
        platform="instagram",
        account_platform_id="ig-scheme",
        account_name="IG",
        connection_status=SocialAccount.ConnectionStatus.CONNECTED,
    )
    return services.issue_api_key(
        workspace=ws, social_accounts=[sa], issued_by=user, name="k", permissions=["create_posts"]
    ).plaintext_token


@pytest.fixture(autouse=True)
def _proxy_and_prod(monkeypatch):
    from apps.jrc_ui import proxy

    monkeypatch.setattr(proxy, "is_trusted_proxy", lambda address: address == PROXY_IP)
    from django.conf import settings

    middleware = [m for m in settings.MIDDLEWARE if m != "apps.jrc_ui.proxy.TrustedProxyMiddleware"]
    with override_settings(
        DEBUG=False,
        SECURE_SSL_REDIRECT=False,
        # Same as config/settings/jrc.py in the running application.
        SECURE_PROXY_SSL_HEADER=("HTTP_X_FORWARDED_PROTO", "https"),
        MIDDLEWARE=["apps.jrc_ui.proxy.TrustedProxyMiddleware", *middleware],
    ):
        yield


def _get(token, remote, proto=None):
    extra = {"REMOTE_ADDR": remote, "HTTP_AUTHORIZATION": f"Bearer {token}"}
    if proto is not None:
        extra["HTTP_X_FORWARDED_PROTO"] = proto
    return Client().get("/api/v1/me/", **extra).status_code


def test_plain_http_without_forwarded_header_is_refused(token):
    assert _get(token, PROXY_IP) == 401


def test_proxy_forwarding_https_is_accepted(token):
    assert _get(token, PROXY_IP, "https") == 200


def test_proxy_saying_http_is_refused(token):
    assert _get(token, PROXY_IP, "http") == 401


def test_forged_https_from_anyone_but_the_proxy_is_ignored(token):
    assert _get(token, OUTSIDE_IP, "https") == 401


def test_forged_https_cannot_help_a_bad_key(token):
    assert _get("bb_studio_not-a-real-key_00000000", PROXY_IP, "https") == 401
