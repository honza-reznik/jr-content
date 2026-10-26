"""P1-B01: klient nemůže podvrhnout IP ani HTTPS hlavičkami mimo bránu."""

from unittest.mock import patch

import pytest
from django.http import HttpResponse
from django.test import RequestFactory, override_settings

from apps.jrc_ui.proxy import TrustedProxyMiddleware
from apps.notifications.engine import action_link
from apps.notifications.models import Notification


@pytest.mark.parametrize("trusted", [False, True])
def test_forwarded_headers_only_from_proxy(trusted):
    request = RequestFactory().post(
        "/accounts/login/",
        REMOTE_ADDR="10.0.0.2",
        HTTP_X_REAL_IP="203.0.113.8",
        HTTP_X_FORWARDED_FOR="fake, 203.0.113.8",
        HTTP_X_FORWARDED_HOST="evil.invalid",
        HTTP_X_FORWARDED_PROTO="https",
    )
    with override_settings(SECURE_PROXY_SSL_HEADER=("HTTP_X_FORWARDED_PROTO", "https")):
        with patch("apps.jrc_ui.proxy.is_trusted_proxy", return_value=trusted):
            TrustedProxyMiddleware(lambda r: HttpResponse())(request)
        assert request.is_secure() is trusted
    assert request.META["REMOTE_ADDR"] == ("203.0.113.8" if trusted else "10.0.0.2")
    assert "HTTP_X_FORWARDED_FOR" not in request.META
    assert "HTTP_X_FORWARDED_HOST" not in request.META


@pytest.mark.parametrize("prefix", ["", "/jrc", "/studio/team"])
def test_old_notifications_and_email_links_follow_prefix(prefix):
    with override_settings(FORCE_SCRIPT_NAME=prefix or None):
        note = Notification(data={"action_url": "/workspace/abc/content/def/?tab=video"})
        assert note.safe_action_url == prefix + "/workspace/abc/content/def/?tab=video"
        assert (
            action_link(note, "https://studio.invalid:10000") == "https://studio.invalid:10000" + note.safe_action_url
        )
        expected = note.safe_action_url
        note.data["action_url"] = expected
        assert note.safe_action_url == expected
        note.data["action_url"] = "//evil.invalid/path"
        assert note.safe_action_url == ""


@pytest.mark.django_db
def test_new_notification_survives_move_to_domain_root():
    from apps.accounts.models import User
    from apps.notifications.engine import notify
    from apps.notifications.models import EventType

    user = User.objects.create_user(email="prefix@example.invalid", password="local-test-only")
    with override_settings(FORCE_SCRIPT_NAME="/jrc"):
        note = notify(user, EventType.CONTENT_SCRIPT_VERSION, "Updated", data={"action_url": "/jrc/workspace/abc/"})
        assert note.data["action_url"] == "/workspace/abc/"
        assert note.safe_action_url == "/jrc/workspace/abc/"
    with override_settings(FORCE_SCRIPT_NAME=None):
        note.refresh_from_db()
        assert note.safe_action_url == "/workspace/abc/"


@pytest.mark.parametrize("base", ["", "/jrc"])
@pytest.mark.parametrize("secure", [True, False])
def test_production_settings_follow_public_address(base, secure):
    import os
    import subprocess
    import sys

    origin = "https://studio.invalid:10000" if secure else "http://127.0.0.1:13443"
    code = """
import os
from config.settings import jrc as s
base = os.environ['JRC_BASE_PATH']
secure = os.environ['JRC_PUBLIC_ORIGIN'].startswith('https:')
assert not s.DEBUG
assert (s.FORCE_SCRIPT_NAME or '') == base
assert s.STATIC_URL == base + '/static/'
assert s.MCP_PUBLIC_BASE_URL == s.MCP_OAUTH_ISSUER_URL == os.environ['JRC_PUBLIC_ORIGIN'] + base
assert s.SESSION_COOKIE_PATH == s.CSRF_COOKIE_PATH == base + '/'
assert s.SESSION_COOKIE_SECURE == s.CSRF_COOKIE_SECURE == secure
assert s.CSRF_TRUSTED_ORIGINS == [os.environ['JRC_PUBLIC_ORIGIN']]
assert not s.SECURE_HSTS_INCLUDE_SUBDOMAINS
assert not s.SECURE_HSTS_PRELOAD
"""
    subprocess.run(
        [sys.executable, "-c", code],
        check=True,
        env={**os.environ, "JRC_MODE": "prod", "JRC_BASE_PATH": base, "JRC_PUBLIC_ORIGIN": origin},
        capture_output=True,
        text=True,
    )
