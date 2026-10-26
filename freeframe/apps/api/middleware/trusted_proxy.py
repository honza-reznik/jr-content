"""Only the shared JR Content proxy may supply client IP and HTTPS scheme."""
import ipaddress
import socket

from ..config import settings


def is_trusted_proxy(peer: str) -> bool:
    if not settings.jrc_trusted_proxy_host:
        return False
    try:
        return peer in {item[4][0] for item in socket.getaddrinfo(settings.jrc_trusted_proxy_host, None)}
    except socket.gaierror:
        return False


def client_ip(request) -> str:
    peer = request.client.host if request.client else "unknown"
    if is_trusted_proxy(peer):
        try:
            return str(ipaddress.ip_address(request.headers.get("x-real-ip", "")))
        except ValueError:
            pass
    return peer


class TrustedProxyMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            peer = (scope.get("client") or ("", 0))[0]
            if is_trusted_proxy(peer):
                headers = dict(scope["headers"])
                if headers.get(b"x-forwarded-proto") == b"https":
                    scope = {**scope, "scheme": "https"}
        await self.app(scope, receive, send)
