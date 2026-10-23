"""Single sign-on ticket ("vstupenka") for FreeFrame (F5-B02).

Format shared with ``freeframe/apps/api/services/jrc_ticket.py``::

    v1.<base64url(JSON claims)>.<base64url(HMAC-SHA256(JRC_FF_SSO_SECRET, "jrc-ff-ticket.v1." + claims part))>

A ticket lives 60 seconds, is single use (FreeFrame stores its nonce with Redis SET NX)
and is bound to one managed account (Brightbean user id + its UUIDv5 FreeFrame id) and
one target: ``web`` (browser sign-in page, may carry ``next``) or ``api`` (this server
calling FreeFrame on behalf of the user, redeemable only on the internal address).
Tickets are never stored or logged.
"""

import base64
import hashlib
import hmac
import json
import secrets
import time

from django.conf import settings
from django.contrib.auth import get_user_model

from .models import VideoAccount

VERSION = "v1"
SIGNING_CONTEXT = b"jrc-ff-ticket.v1."
ISSUER = "jrc-brightbean"
AUDIENCES = frozenset({"web", "api"})
LIFETIME_SECONDS = 60
MIN_SECRET_LENGTH = 32
MAX_NEXT_LENGTH = 512


class TicketError(Exception):
    pass


def _b64encode(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _secret():
    secret = getattr(settings, "JRC_FF_SSO_SECRET", "") or ""
    if len(secret) < MIN_SECRET_LENGTH:
        raise TicketError("JRC_FF_SSO_SECRET is missing or too short (run make env).")
    return secret.encode()


def sign(payload_part, secret):
    digest = hmac.new(secret, SIGNING_CONTEXT + payload_part.encode("ascii"), hashlib.sha256).digest()
    return _b64encode(digest)


def is_safe_next(value):
    """A path inside the FreeFrame web app; never another host or scheme."""
    return (
        isinstance(value, str)
        and 1 <= len(value) <= MAX_NEXT_LENGTH
        and value.startswith("/")
        and not value.startswith(("//", "/\\"))
        and "\\" not in value
        and not any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in value)
    )


def encode_ticket(claims, secret):
    payload = _b64encode(json.dumps(claims, separators=(",", ":"), sort_keys=True).encode())
    return f"{VERSION}.{payload}.{sign(payload, secret)}"


def issue_ticket(user, *, audience, next_path=None, now=None):
    """Return a fresh ticket for an active, synchronized user."""
    if audience not in AUDIENCES:
        raise ValueError(f"Unknown ticket audience: {audience!r}")
    if next_path is not None and (audience != "web" or not is_safe_next(next_path)):
        raise ValueError("next_path must be a FreeFrame web path and is allowed only for web tickets.")
    secret = _secret()
    # Re-read from the database: a blocked user never gets a ticket, even with a stale object.
    if not get_user_model().objects.filter(pk=user.pk, is_active=True).exists():
        raise TicketError("Inactive or deleted users cannot sign in to FreeFrame.")
    account = VideoAccount.objects.filter(user_id=user.pk).first()
    if account is None:
        raise TicketError("The user has no FreeFrame account yet; synchronize it first.")
    issued = int(time.time() if now is None else now)
    claims = {
        "v": 1,
        "iss": ISSUER,
        "aud": audience,
        "acct": str(user.pk),
        "sub": str(account.ff_user_id),
        "iat": issued,
        "exp": issued + LIFETIME_SECONDS,
        "nonce": secrets.token_urlsafe(24),
    }
    if next_path is not None:
        claims["next"] = next_path
    return encode_ticket(claims, secret)
