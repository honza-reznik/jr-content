"""P1-B01: trusted client identity and HTTPS behind the shared entrance."""
from unittest.mock import patch

import pytest
from starlette.requests import Request

from apps.api.middleware.trusted_proxy import client_ip, TrustedProxyMiddleware
from apps.api.services.s3_service import _browser_url
from apps.api.config import settings


@pytest.mark.parametrize("trusted", [True, False])
def test_ip_cannot_be_spoofed_by_untrusted_peer(trusted):
    request = Request({"type": "http", "client": ("10.0.0.2", 12),
                       "headers": [(b"x-real-ip", b"203.0.113.8")]})
    with patch("apps.api.middleware.trusted_proxy.is_trusted_proxy", return_value=trusted):
        assert client_ip(request) == ("203.0.113.8" if trusted else "10.0.0.2")


@pytest.mark.asyncio
@pytest.mark.parametrize("trusted", [True, False])
async def test_https_scheme_only_from_trusted_proxy(trusted):
    seen = []
    async def app(scope, receive, send):
        seen.append(scope["scheme"])
    scope = {"type": "http", "client": ("10.0.0.2", 12), "scheme": "http",
             "headers": [(b"x-forwarded-proto", b"https")]}
    with patch("apps.api.middleware.trusted_proxy.is_trusted_proxy", return_value=trusted):
        await TrustedProxyMiddleware(app)(scope, None, None)
    assert seen == ["https" if trusted else "http"]


def test_browser_transport_prefix_preserves_signature_and_query(monkeypatch):
    monkeypatch.setattr(settings, "s3_storage", "minio")
    monkeypatch.setattr(settings, "s3_public_endpoint", "https://studio.invalid:10000")
    monkeypatch.setattr(settings, "s3_public_path", "/jrc/storage")
    signed = "https://studio.invalid:10000/jrc-video/raw/a%20b.mp4?X-Amz-Signature=abc&partNumber=1"
    assert _browser_url(signed) == signed.replace("/jrc-video/", "/jrc/storage/jrc-video/")
