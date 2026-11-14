"""Bounded public favicon retrieval; also used by the credential-free relay.

No ambient HTTP proxy, cookies or credentials. Every redirect is resolved and
checked again; sockets connect to the validated IP, TLS still verifies the host.
The relay runs this in a subprocess with an eight-second wall-clock deadline,
including DNS and slow trickle responses.
"""

import http.client
import ipaddress
import socket
import ssl
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

MAX_BYTES = 1024 * 1024
MAX_HTML = 256 * 1024


class FetchError(ValueError):
    pass


def public_target(url):
    parts = urlsplit(url)
    if (
        len(url) > 2000
        or parts.scheme not in {"http", "https"}
        or not parts.hostname
        or parts.username is not None
        or parts.password is not None
        or parts.port not in {None, 80, 443}
        or any(ord(c) < 33 or ord(c) == 127 for c in url)
    ):
        raise FetchError("Invalid public URL")
    host = parts.hostname.encode("idna").decode("ascii")
    port = parts.port or (443 if parts.scheme == "https" else 80)
    addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(row[4][0]).is_global for row in addresses):
        raise FetchError("Private or reserved address")
    # Reject mapped/transition IPv6 addresses as well as ordinary private ranges.
    for row in addresses:
        address = ipaddress.ip_address(row[4][0])
        if address.version == 6 and (
            address.ipv4_mapped
            or address.sixtofour
            or address.teredo
            or address in ipaddress.IPv6Network("64:ff9b::/96")
            or address in ipaddress.IPv6Network("64:ff9b:1::/48")
        ):
            raise FetchError("Transition address")
    return parts, host, port, addresses[0]


class PinnedConnection(http.client.HTTPConnection):
    def __init__(self, host, port, address, *, tls):
        super().__init__(host, port, timeout=2)
        self.address = address
        self.tls = tls

    def connect(self):
        family, socktype, proto, _, sockaddr = self.address
        raw = socket.socket(family, socktype, proto)
        raw.settimeout(self.timeout)
        try:
            raw.connect(sockaddr)
            self.sock = ssl.create_default_context().wrap_socket(raw, server_hostname=self.host) if self.tls else raw
        except BaseException:
            raw.close()
            raise


def fetch(url, limit):
    for _ in range(4):
        parts, host, port, address = public_target(url)
        connection = PinnedConnection(host, port, address, tls=parts.scheme == "https")
        try:
            connection.request(
                "GET",
                (parts.path or "/") + ("?" + parts.query if parts.query else ""),
                headers={"User-Agent": "JRContent-Favicon/1.0", "Accept-Encoding": "identity"},
            )
            response = connection.getresponse()
            if response.status in {301, 302, 303, 307, 308}:
                location = response.getheader("Location")
                if not location:
                    raise FetchError("Missing redirect target")
                url = urljoin(url, location)
                continue
            if response.status != 200 or response.getheader("Content-Encoding", "identity") != "identity":
                raise FetchError("Image unavailable")
            length = response.getheader("Content-Length")
            if length is not None and (not length.isdecimal() or int(length) > limit):
                raise FetchError("Response too large")
            body = response.read(limit + 1)
            if not body or len(body) > limit:
                raise FetchError("Response too large or empty")
            return body, response.getheader("Content-Type", "").split(";", 1)[0].lower(), url
        finally:
            connection.close()
    raise FetchError("Too many redirects")


class IconLinks(HTMLParser):
    def __init__(self):
        super().__init__()
        self.href = None

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        rel = values.get("rel") or ""
        if tag == "link" and "icon" in rel.lower().split() and not self.href:
            self.href = values.get("href")


def download_favicon(profile_url):
    # Validate the supplied URL even though the fallback uses only its origin.
    parts, _, _, _ = public_target(profile_url)
    fallback = f"{parts.scheme}://{parts.netloc}/favicon.ico"
    try:
        html, content_type, final_url = fetch(profile_url, MAX_HTML)
        if content_type == "text/html":
            parser = IconLinks()
            parser.feed(html.decode("utf-8", errors="replace"))
            if parser.href:
                body, content_type, _ = fetch(urljoin(final_url, parser.href), MAX_BYTES)
                if not content_type.startswith("image/"):
                    raise FetchError("Not an image")
                return body
    except (OSError, ValueError, http.client.HTTPException):
        pass
    body, content_type, _ = fetch(fallback, MAX_BYTES)
    if not content_type.startswith("image/"):
        raise FetchError("Not an image")
    return body
