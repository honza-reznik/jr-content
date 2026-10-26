"""P1-B01: forwarded headers patří jen společné bráně, nikoli klientovi."""

import ipaddress
import socket
from contextlib import suppress


def is_trusted_proxy(address):
    try:
        return address in {item[4][0] for item in socket.getaddrinfo("jrc-proxy", None)}
    except socket.gaierror:
        return False


class TrustedProxyMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        meta = request.META
        trusted = is_trusted_proxy(meta.get("REMOTE_ADDR"))
        real_ip = meta.pop("HTTP_X_REAL_IP", "")
        # Caddy určil IP z důvěryhodného řetězce. Zbytek aplikace čte jedinou
        # adresu; surovou XFF odstraníme i kvůli upstreamovým limiterům.
        meta.pop("HTTP_X_FORWARDED_FOR", None)
        meta.pop("HTTP_X_FORWARDED_HOST", None)
        if trusted:
            with suppress(ValueError):
                meta["REMOTE_ADDR"] = str(ipaddress.ip_address(real_ip))
        else:
            meta.pop("HTTP_X_FORWARDED_PROTO", None)
        return self.get_response(request)
