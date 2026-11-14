# JR Content: přidáno 2026-11-14 (blok P2-B17), AGPL-3.0.
"""Optional two-factor authentication with an authenticator app (TOTP).

Everything that reads or changes a user's second factor goes through this
module, so the rules live in one place:

* TOTP per RFC 6238 (HMAC-SHA1, 6 digits, 30 s steps) with a tolerance of one
  step either side. A code is accepted only for a time step *after* the last
  accepted one (``User.totp_last_counter``), so the same code - or an older
  one from the tolerance window - cannot be replayed, even by a parallel
  request: the check runs under a row lock.
* The secret lives only in ``User.totp_secret`` (AES-GCM encrypted field). It
  is shown to its owner during setup and never logged.
* Recovery codes are single-use, shown once and stored only as keyed hashes
  (HMAC-SHA256 under a key derived from ``SECRET_KEY``, bound to the user).
* Wrong codes (and wrong passwords on the security page) count against a
  per-user budget in the shared cache: 5 per 15 minutes and 20 per day. Past
  either limit every check fails as locked, whatever is typed. A completed
  password reset clears the budget (see ``clear_failures``).
* Every change is written to ``TwoFactorEvent`` (the audit trail).
"""

from __future__ import annotations

import base64
import enum
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote, urlencode

from django.core.cache import cache
from django.db import transaction
from django.utils import timezone
from django.utils.crypto import salted_hmac
from django.views.decorators.debug import sensitive_variables

from apps.accounts.models import TwoFactorEvent, User

Action = TwoFactorEvent.Action

PERIOD = 30
DIGITS = 6
TOLERANCE = 1
ISSUER = "JR Content"
RECOVERY_CODE_COUNT = 10
# Ambiguous characters (0/o, 1/l/i) left out; 10 characters ≈ 49 bits each.
RECOVERY_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"
RECOVERY_CODE_LENGTH = 10
FAILURE_LIMITS = ((5, 15 * 60), (20, 24 * 60 * 60))


class Result(enum.Enum):
    TOTP = "totp"
    RECOVERY_CODE = "recovery_code"
    INVALID = "invalid"
    REUSED = "reused"
    LOCKED = "locked"

    @property
    def ok(self) -> bool:
        return self in (Result.TOTP, Result.RECOVERY_CODE)


ERROR_MESSAGES = {
    Result.INVALID: "That code is not valid. Check the code in your authenticator app and try again.",
    Result.REUSED: "That code was already used. Wait for the next code in your authenticator app.",
    Result.LOCKED: "Too many incorrect attempts. Wait 15 minutes and try again, or reset your password by email.",
}


# -- RFC 4226 / RFC 6238 ------------------------------------------------------


def generate_secret() -> str:
    """160 random bits, base32 without padding (32 characters), as RFC 4226 recommends."""
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")


@sensitive_variables()
def _secret_bytes(secret: str) -> bytes:
    cleaned = secret.replace(" ", "").upper()
    return base64.b32decode(cleaned + "=" * (-len(cleaned) % 8))


@sensitive_variables()
def hotp(key: bytes, counter: int, digits: int = DIGITS, digest=hashlib.sha1) -> str:
    mac = hmac.new(key, struct.pack(">Q", counter), digest).digest()
    offset = mac[-1] & 0x0F
    value = struct.unpack(">I", mac[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(value % 10**digits).zfill(digits)


@sensitive_variables()
def totp(key: bytes, at: float, digits: int = DIGITS, digest=hashlib.sha1, period: int = PERIOD) -> str:
    return hotp(key, int(at) // period, digits, digest)


def normalize_code(code: str) -> str:
    # ASCII only: str.isdigit()/isalnum() also accept other scripts' digits (e.g. fullwidth),
    # which hmac.compare_digest() rejects with a TypeError.
    return "".join(ch for ch in (code or "") if ch.isascii() and ch.isalnum()).lower()


@sensitive_variables()
def matching_counter(secret: str, code: str, now: float | None = None) -> int | None:
    """Time step whose code equals ``code`` within ±TOLERANCE steps, else None."""
    code = normalize_code(code)
    if len(code) != DIGITS or not (code.isascii() and code.isdigit()):
        return None
    key = _secret_bytes(secret)
    current = int(time.time() if now is None else now) // PERIOD
    found = None
    # Compare every candidate so the timing does not reveal which step matched.
    for counter in range(current - TOLERANCE, current + TOLERANCE + 1):
        if hmac.compare_digest(hotp(key, counter), code) and found is None:
            found = counter
    return found


@sensitive_variables()
def otpauth_uri(user, secret: str) -> str:
    label = quote(f"{ISSUER}:{user.email}")
    params = urlencode({"secret": secret, "issuer": ISSUER}, quote_via=quote)
    return f"otpauth://totp/{label}?{params}"


def format_secret(secret: str) -> str:
    return " ".join(secret[i : i + 4] for i in range(0, len(secret), 4))


# -- recovery codes -------------------------------------------------------------


def _generate_recovery_code() -> str:
    raw = "".join(secrets.choice(RECOVERY_ALPHABET) for _ in range(RECOVERY_CODE_LENGTH))
    half = RECOVERY_CODE_LENGTH // 2
    return f"{raw[:half]}-{raw[half:]}"


@sensitive_variables()
def hash_recovery_code(user, code: str) -> str:
    return salted_hmac(
        "jrc.accounts.twofactor.recovery", f"{user.pk}:{normalize_code(code)}", algorithm="sha256"
    ).hexdigest()


def _new_recovery_codes(user) -> tuple[list[str], list[str]]:
    codes = [_generate_recovery_code() for _ in range(RECOVERY_CODE_COUNT)]
    return codes, [hash_recovery_code(user, code) for code in codes]


def recovery_codes_left(user) -> int:
    return len(user.totp_recovery_codes or [])


# -- failure budget ---------------------------------------------------------------


def _failure_keys(user):
    return [(f"jrc2fa:fail:{user.pk}:{window}", limit, window) for limit, window in FAILURE_LIMITS]


def is_locked(user) -> bool:
    return any((cache.get(key) or 0) >= limit for key, limit, _ in _failure_keys(user))


def register_failure(user, request=None) -> None:
    newly_locked = False
    for key, limit, window in _failure_keys(user):
        if cache.add(key, 1, window):
            attempts = 1
        else:
            try:
                attempts = cache.incr(key)
            except ValueError:
                cache.add(key, 1, window)
                attempts = 1
        newly_locked = newly_locked or attempts == limit
    if newly_locked:
        log_event(user, Action.LOCKED, request=request)


def clear_failures(user) -> None:
    cache.delete_many([key for key, _, _ in _failure_keys(user)])


# -- audit ----------------------------------------------------------------------


def _client_ip(request):
    if request is None:
        return None
    return request.META.get("REMOTE_ADDR") or None


def log_event(user, action, *, actor=None, organization=None, request=None):
    return TwoFactorEvent.objects.create(
        user=user,
        action=action,
        actor=actor,
        actor_email=getattr(actor, "email", "") or "",
        organization=organization,
        ip_address=_client_ip(request),
    )


# -- state changes ------------------------------------------------------------------


def is_enabled(user) -> bool:
    return bool(getattr(user, "totp_enabled", False))


def _locked_row(user):
    return User.objects.select_for_update().get(pk=user.pk)


def _refresh(user, row) -> None:
    for field in ("totp_secret", "totp_recovery_codes", "totp_enabled", "totp_enabled_at", "totp_last_counter"):
        setattr(user, field, getattr(row, field))


@sensitive_variables()
def pending_secret(user, *, regenerate: bool = False) -> str:
    """Secret for a setup in progress; created on first use and kept until confirmed or cancelled."""
    with transaction.atomic():
        row = _locked_row(user)
        if row.totp_enabled:
            raise ValueError("Two-factor authentication is already on.")
        if regenerate or not row.totp_secret:
            row.totp_secret = generate_secret()
            row.totp_last_counter = None
            row.save(update_fields=["totp_secret", "totp_last_counter", "updated_at"])
        _refresh(user, row)
        return row.totp_secret


def cancel_setup(user) -> None:
    with transaction.atomic():
        row = _locked_row(user)
        if not row.totp_enabled:
            row.totp_secret = None
            row.totp_last_counter = None
            row.save(update_fields=["totp_secret", "totp_last_counter", "updated_at"])
        _refresh(user, row)


@sensitive_variables()
def confirm_setup(user, code: str, request=None) -> tuple[Result, list[str]]:
    """Turn 2FA on if ``code`` matches the pending secret; returns the new recovery codes."""
    with transaction.atomic():
        row = _locked_row(user)
        # The budget check and the counting of a failed attempt both happen under the row lock,
        # so parallel requests cannot all pass the check before any of them is counted.
        if is_locked(user):
            return Result.LOCKED, []
        if row.totp_enabled or not row.totp_secret:
            return Result.INVALID, []
        counter = matching_counter(row.totp_secret, code)
        if counter is None:
            result = Result.INVALID
        else:
            codes, hashes = _new_recovery_codes(row)
            row.totp_enabled = True
            row.totp_enabled_at = timezone.now()
            row.totp_last_counter = counter
            row.totp_recovery_codes = hashes
            row.save(
                update_fields=[
                    "totp_enabled",
                    "totp_enabled_at",
                    "totp_last_counter",
                    "totp_recovery_codes",
                    "updated_at",
                ]
            )
            log_event(row, Action.ENABLED, actor=row, request=request)
            _refresh(user, row)
            clear_failures(user)
            return Result.TOTP, codes
        register_failure(user, request)
    return result, []


@sensitive_variables()
def verify(user, code: str, request=None, *, allow_recovery: bool = True) -> Result:
    """Check a second-factor code for ``user`` (login step and sensitive changes).

    A matching app code is consumed (``totp_last_counter``); a matching
    recovery code is removed. Any failure counts against the budget.
    """
    normalized = normalize_code(code)
    result = Result.INVALID
    with transaction.atomic():
        row = _locked_row(user)
        # Check and count under the row lock (see confirm_setup).
        if is_locked(user):
            return Result.LOCKED
        if not row.totp_enabled or not row.totp_secret:
            return Result.INVALID
        if len(normalized) == DIGITS and normalized.isascii() and normalized.isdigit():
            counter = matching_counter(row.totp_secret, normalized)
            if counter is not None:
                if row.totp_last_counter is not None and counter <= row.totp_last_counter:
                    result = Result.REUSED
                else:
                    row.totp_last_counter = counter
                    row.save(update_fields=["totp_last_counter", "updated_at"])
                    result = Result.TOTP
        elif allow_recovery and len(normalized) == RECOVERY_CODE_LENGTH:
            digest = hash_recovery_code(row, normalized)
            stored = list(row.totp_recovery_codes or [])
            match = next((h for h in stored if hmac.compare_digest(h, digest)), None)
            if match is not None:
                stored.remove(match)
                row.totp_recovery_codes = stored
                row.save(update_fields=["totp_recovery_codes", "updated_at"])
                log_event(row, Action.RECOVERY_CODE_USED, actor=row, request=request)
                result = Result.RECOVERY_CODE
        _refresh(user, row)
        if result.ok:
            clear_failures(user)
        else:
            register_failure(user, request)
    return result


def regenerate_recovery_codes(user, request=None) -> list[str]:
    with transaction.atomic():
        row = _locked_row(user)
        if not row.totp_enabled:
            raise ValueError("Two-factor authentication is off.")
        codes, hashes = _new_recovery_codes(row)
        row.totp_recovery_codes = hashes
        row.save(update_fields=["totp_recovery_codes", "updated_at"])
        log_event(row, Action.RECOVERY_CODES_REGENERATED, actor=row, request=request)
        _refresh(user, row)
    return codes


def _turn_off(user, action, *, actor, organization=None, request=None) -> bool:
    with transaction.atomic():
        row = _locked_row(user)
        was_on = row.totp_enabled
        row.totp_enabled = False
        row.totp_enabled_at = None
        row.totp_secret = None
        row.totp_recovery_codes = None
        row.totp_last_counter = None
        row.save(
            update_fields=[
                "totp_enabled",
                "totp_enabled_at",
                "totp_secret",
                "totp_recovery_codes",
                "totp_last_counter",
                "updated_at",
            ]
        )
        if was_on:
            log_event(row, action, actor=actor, organization=organization, request=request)
        _refresh(user, row)
    clear_failures(user)
    return was_on


def disable(user, request=None) -> bool:
    return _turn_off(user, Action.DISABLED, actor=user, request=request)


def reset_by_admin(user, *, actor, organization, request=None) -> bool:
    return _turn_off(user, Action.RESET_BY_ADMIN, actor=actor, organization=organization, request=request)
