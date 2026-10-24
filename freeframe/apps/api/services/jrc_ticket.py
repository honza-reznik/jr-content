"""JR Content single sign-on ticket ("vstupenka", F5-B02).

Brightbean issues the ticket (``brightbean/apps/jrc_video/tickets.py``), FreeFrame
redeems it once at ``POST /auth/jrc-exchange``. Format::

    v1.<base64url(JSON claims)>.<base64url(HMAC-SHA256(secret, "jrc-ff-ticket.v1." + claims part))>

The signature is checked (constant time) before the claims are decoded, so no
untrusted JSON is ever parsed. Both implementations share a known-answer test
vector (``tests/test_jrc_exchange.py`` and ``apps/jrc_video/tests/test_tickets.py``).
"""

import base64
import binascii
import hashlib
import hmac
import json
import os
import re
import time
import uuid

from ..routers.jrc_sync import remote_id

VERSION = "v1"
SIGNING_CONTEXT = b"jrc-ff-ticket.v1."
ISSUER = "jrc-brightbean"
# "web": browser sign-in page (/jrc-sso, F5-B03); "api": Brightbean server acting for the user.
AUDIENCES = frozenset({"web", "api"})
MAX_LIFETIME_SECONDS = 60
MAX_CLOCK_SKEW_SECONDS = 5
# Longer than any ticket can live (iat <= now + skew, exp <= iat + lifetime).
NONCE_TTL_SECONDS = 120
NONCE_PREFIX = "jrc_sso_nonce:"
MIN_SECRET_LENGTH = 32
MAX_TICKET_LENGTH = 2048
MAX_NEXT_LENGTH = 512

_B64URL = re.compile(r"^[A-Za-z0-9_-]+$")
_NONCE = re.compile(r"^[A-Za-z0-9_-]{22,64}$")
_REQUIRED = frozenset({"v", "iss", "aud", "acct", "sub", "iat", "exp", "nonce"})
_OPTIONAL = frozenset({"next"})


class TicketError(Exception):
    """Rejected ticket. ``reason`` is for server logs only, never for the response."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def sso_secret() -> bytes | None:
    secret = os.environ.get("JRC_FF_SSO_SECRET", "")
    if len(secret) < MIN_SECRET_LENGTH:
        return None
    return secret.encode()


def _b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64decode(part: str) -> bytes:
    if not _B64URL.match(part):
        raise TicketError("malformed encoding")
    try:
        data = base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))
    except (binascii.Error, ValueError):
        raise TicketError("malformed encoding") from None
    if _b64encode(data) != part:
        raise TicketError("non-canonical encoding")
    return data


def signature(payload_part: str, secret: bytes) -> str:
    digest = hmac.new(secret, SIGNING_CONTEXT + payload_part.encode("ascii"), hashlib.sha256).digest()
    return _b64encode(digest)


def is_safe_next(value) -> bool:
    """A path inside the FreeFrame web app; never another host or scheme."""
    return (
        isinstance(value, str)
        and 1 <= len(value) <= MAX_NEXT_LENGTH
        and value.startswith("/")
        and value.split("?", 1)[0].split("/")[1] in {"projects", "assets", "notifications", "settings"}
        and not value.startswith(("//", "/\\"))
        and "\\" not in value
        and not any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in value)
    )


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def verify_ticket(ticket: str, *, secret: bytes, now: float | None = None) -> dict:
    """Return the claims of an authentic, unexpired ticket or raise TicketError.

    Single use is NOT checked here; the caller consumes the nonce (consume_nonce).
    """
    if not isinstance(ticket, str) or not ticket.isascii() or len(ticket) > MAX_TICKET_LENGTH:
        raise TicketError("malformed ticket")
    parts = ticket.split(".")
    if len(parts) != 3 or parts[0] != VERSION:
        raise TicketError("unknown format")
    payload_part, signature_part = parts[1], parts[2]
    if not payload_part or not _B64URL.match(payload_part):
        raise TicketError("malformed encoding")
    if not hmac.compare_digest(signature(payload_part, secret).encode(), signature_part.encode()):
        raise TicketError("bad signature")
    try:
        claims = json.loads(_b64decode(payload_part))
    except (ValueError, UnicodeDecodeError):
        raise TicketError("malformed claims") from None
    if not isinstance(claims, dict) or not _REQUIRED <= claims.keys() <= _REQUIRED | _OPTIONAL:
        raise TicketError("unexpected claims")
    # bool is an int in Python (True == 1); the version must be exactly the integer 1.
    if not _is_int(claims["v"]) or claims["v"] != 1 or claims["iss"] != ISSUER:
        raise TicketError("wrong version or issuer")
    if claims["aud"] not in AUDIENCES:
        raise TicketError("wrong audience")
    try:
        source_id = uuid.UUID(claims["acct"])
        subject = uuid.UUID(claims["sub"])
    except (ValueError, TypeError, AttributeError):
        raise TicketError("malformed account") from None
    # Bound to one managed account: the FreeFrame id must be derived from the Brightbean id.
    if subject != remote_id("account", source_id) or claims["sub"] != str(subject):
        raise TicketError("account binding mismatch")
    issued, expires = claims["iat"], claims["exp"]
    if not _is_int(issued) or not _is_int(expires):
        raise TicketError("malformed times")
    current = time.time() if now is None else now
    if not 0 < expires - issued <= MAX_LIFETIME_SECONDS:
        raise TicketError("lifetime too long")
    if issued > current + MAX_CLOCK_SKEW_SECONDS:
        raise TicketError("issued in the future")
    if current >= expires:
        raise TicketError("expired")
    if not isinstance(claims["nonce"], str) or not _NONCE.match(claims["nonce"]):
        raise TicketError("malformed nonce")
    if "next" in claims and (claims["aud"] != "web" or not is_safe_next(claims["next"])):
        raise TicketError("invalid next path")
    return claims


def consume_nonce(redis_client, nonce: str) -> bool:
    """Atomically mark the nonce as used (Redis SET NX). False = already used."""
    key = NONCE_PREFIX + hashlib.sha256(nonce.encode()).hexdigest()
    return bool(redis_client.set(key, "1", nx=True, ex=NONCE_TTL_SECONDS))
