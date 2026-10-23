"""JR Content ticket exchange (F5-B02): POST /auth/jrc-exchange and the ticket format.

Attack cases: replay (sequential and racing), expiry, forged/tampered signature,
another account, non-existing/deactivated/deleted/privileged account, wrong audience,
open redirect through ``next``, format confusion with FreeFrame JWTs, Redis outage.
"""

import base64
import json
import os
import threading
import time
import uuid

import jwt
import pytest

from apps.api.models.user import UserStatus
from apps.api.routers.jrc_sync import remote_id
from apps.api.services import jrc_ticket
from apps.api.services.jrc_ticket import consume_nonce, signature, verify_ticket
from apps.api.tests.conftest import _make_user

SECRET = "s" * 64
ACCOUNT = uuid.UUID("f5b02000-0000-4000-8000-000000000101")
OTHER_ACCOUNT = uuid.UUID("f5b02000-0000-4000-8000-000000000102")


class FakeRedis:
    """SET NX/EX semantics of Redis, thread safe."""

    def __init__(self):
        self.store = {}
        self.calls = []
        self.lock = threading.Lock()

    def set(self, key, value, nx=False, ex=None):
        with self.lock:
            self.calls.append({"key": key, "nx": nx, "ex": ex})
            if nx and key in self.store:
                return None
            self.store[key] = value
            return True


class BrokenRedis:
    def set(self, *args, **kwargs):
        raise ConnectionError("redis down")


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def make_ticket(secret=SECRET, *, raw=None, **overrides):
    now = int(time.time())
    claims = {
        "v": 1,
        "iss": "jrc-brightbean",
        "aud": "web",
        "acct": str(ACCOUNT),
        "sub": str(remote_id("account", ACCOUNT)),
        "iat": now,
        "exp": now + 60,
        "nonce": uuid.uuid4().hex,
    }
    claims.update(overrides)
    claims = {key: value for key, value in claims.items() if value is not None}
    payload = raw if raw is not None else b64(json.dumps(claims, separators=(",", ":"), sort_keys=True).encode())
    secret_bytes = secret if isinstance(secret, bytes) else secret.encode()
    return f"v1.{payload}.{signature(payload, secret_bytes)}"


@pytest.fixture
def redis(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr("apps.api.routers.jrc_auth.get_redis", lambda: fake)
    return fake


@pytest.fixture(autouse=True)
def sso_env(monkeypatch):
    monkeypatch.setenv("JRC_FF_SSO_SECRET", SECRET)
    monkeypatch.setenv("JRC_FF_SYNC_EMAIL", "jrc-sync@example.com")
    # Keep the shared-IP global limit out of these unauthenticated requests (fails open).
    monkeypatch.setattr("apps.api.middleware.global_rate_limit.get_redis", BrokenRedis)


@pytest.fixture
def managed_user(mock_db):
    user = _make_user(user_id=remote_id("account", ACCOUNT), email="ella@example.invalid")
    user.password_hash = None
    user.token_version = 3
    mock_db.first.return_value = user
    return user


def exchange(client, ticket, headers=None):
    return client.post("/auth/jrc-exchange", json={"ticket": ticket}, headers=headers or {})


def assert_rejected(response, redis=None, *, consumed=False):
    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid or expired ticket"}
    assert "access_token" not in response.text
    assert response.headers["cache-control"] == "no-store"
    if redis is not None and not consumed:
        assert redis.calls == []


# --- valid tickets ------------------------------------------------------------------


def test_web_ticket_returns_tokens_and_next(client, redis, managed_user):
    response = exchange(client, make_ticket(next="/projects/p/assets/a?embed=1"))
    assert response.status_code == 200
    body = response.json()
    assert response.headers["cache-control"] == "no-store"
    assert body["token_type"] == "bearer" and body["expires_in"] == 15 * 60
    assert body["next"] == "/projects/p/assets/a?embed=1"
    access = jwt.decode(body["access_token"], os.environ["JWT_SECRET"], algorithms=["HS256"])
    refresh = jwt.decode(body["refresh_token"], os.environ["JWT_SECRET"], algorithms=["HS256"])
    assert access["sub"] == refresh["sub"] == str(managed_user.id)
    assert access["type"] == "access" and refresh["type"] == "refresh"
    assert access["ver"] == refresh["ver"] == 3
    assert redis.calls[0]["nx"] is True and redis.calls[0]["ex"] == 120
    # The raw nonce is not stored as the key.
    assert all(len(key) == len("jrc_sso_nonce:") + 64 for key in redis.store)


def test_api_ticket_gets_only_access_token(client, redis, managed_user):
    response = exchange(client, make_ticket(aud="api"))
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"access_token", "token_type", "expires_in"}


def test_web_ticket_without_next(client, redis, managed_user):
    response = exchange(client, make_ticket())
    assert response.status_code == 200
    assert "next" not in response.json() and "refresh_token" in response.json()


# --- single use -------------------------------------------------------------------


def test_ticket_cannot_be_used_twice(client, redis, managed_user):
    ticket = make_ticket()
    assert exchange(client, ticket).status_code == 200
    assert_rejected(exchange(client, ticket), redis, consumed=True)
    assert len(redis.store) == 1


def test_racing_requests_with_one_ticket_only_one_wins(client, redis, managed_user):
    ticket = make_ticket(aud="api")
    results = []
    barrier = threading.Barrier(8)

    def worker():
        barrier.wait()
        results.append(exchange(client, ticket).status_code)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(results) == [200] + [401] * 7


def test_nonce_is_spent_even_when_account_check_fails(client, redis, mock_db, managed_user):
    managed_user.status = UserStatus.deactivated
    ticket = make_ticket()
    assert_rejected(exchange(client, ticket), redis, consumed=True)
    # Reactivated within the minute: the same ticket still does not work.
    managed_user.status = UserStatus.active
    assert_rejected(exchange(client, ticket), redis, consumed=True)


def test_resigned_ticket_with_same_nonce_is_replay(client, redis, managed_user):
    nonce = uuid.uuid4().hex
    assert exchange(client, make_ticket(nonce=nonce)).status_code == 200
    assert_rejected(exchange(client, make_ticket(nonce=nonce, aud="api")), redis, consumed=True)


def test_redis_outage_fails_closed(client, monkeypatch, managed_user):
    monkeypatch.setattr("apps.api.routers.jrc_auth.get_redis", BrokenRedis)
    response = exchange(client, make_ticket())
    assert response.status_code == 503
    assert "access_token" not in response.text


# --- time --------------------------------------------------------------------------


def test_expired_ticket(client, redis, mock_db, managed_user):
    now = int(time.time())
    assert_rejected(exchange(client, make_ticket(iat=now - 61, exp=now - 1)), redis)
    assert_rejected(exchange(client, make_ticket(iat=now - 60, exp=now)), redis)
    mock_db.query.assert_not_called()


def test_lifetime_longer_than_60_seconds(client, redis, managed_user):
    now = int(time.time())
    assert_rejected(exchange(client, make_ticket(iat=now, exp=now + 61)), redis)
    assert_rejected(exchange(client, make_ticket(iat=now, exp=now)), redis)
    assert_rejected(exchange(client, make_ticket(iat=now, exp=now - 5)), redis)


def test_ticket_issued_in_the_future(client, redis, managed_user):
    now = int(time.time())
    assert_rejected(exchange(client, make_ticket(iat=now + 30, exp=now + 60)), redis)


@pytest.mark.parametrize("value", [True, 1.5, "1700000000", None])
def test_times_must_be_integers(client, redis, managed_user, value):
    now = int(time.time())
    claims = {"iat": value, "exp": now + 60} if value is not None else {"iat": None}
    assert_rejected(exchange(client, make_ticket(**claims)), redis)


# --- signature and format ---------------------------------------------------------------


def test_wrong_secret(client, redis, managed_user):
    assert_rejected(exchange(client, make_ticket(secret="x" * 64)), redis)


def test_signed_with_freeframe_jwt_secret(client, redis, managed_user):
    assert_rejected(exchange(client, make_ticket(secret=os.environ["JWT_SECRET"])), redis)


def test_tampered_claims_keep_old_signature(client, redis, managed_user):
    original = make_ticket()
    _, payload, sig = original.split(".")
    claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    claims["acct"] = str(OTHER_ACCOUNT)
    claims["sub"] = str(remote_id("account", OTHER_ACCOUNT))
    forged = f"v1.{b64(json.dumps(claims).encode())}.{sig}"
    assert_rejected(exchange(client, forged), redis)


@pytest.mark.parametrize("mutate", [
    lambda t: t[:-1] + ("A" if t[-1] != "A" else "B"),
    lambda t: t.rsplit(".", 1)[0] + ".",
    lambda t: t.rsplit(".", 1)[0],
    lambda t: t.replace("v1.", "v2.", 1),
    lambda t: t.replace("v1.", "", 1),
    lambda t: t + "=",
    lambda t: t + ".extra",
    lambda t: t.rsplit(".", 1)[0] + "." + base64.urlsafe_b64encode(b"\0" * 32).decode(),
])
def test_tampered_or_truncated_tickets(client, redis, managed_user, mutate):
    assert_rejected(exchange(client, mutate(make_ticket())), redis)


def test_freeframe_access_token_is_not_a_ticket(client, redis, managed_user):
    from apps.api.services.auth_service import create_access_token

    assert_rejected(exchange(client, create_access_token(str(managed_user.id))), redis)


@pytest.mark.parametrize("text", ["aR", "aQ==", "a+Q", "a/Q", "a"])
def test_non_canonical_or_foreign_base64_is_rejected(text):
    # "aR" decodes to the same byte as the canonical "aQ"; padding and +/ are not base64url.
    assert jrc_ticket._b64decode("aQ") == b"i"
    with pytest.raises(jrc_ticket.TicketError):
        jrc_ticket._b64decode(text)


def test_signed_garbage_payload_is_rejected(client, redis, managed_user):
    for raw in [b64(b"not json"), b64(b"[1,2]"), b64(json.dumps({"v": 1}).encode()), b64(b"\xff\xfe")]:
        assert_rejected(exchange(client, make_ticket(raw=raw)), redis)


@pytest.mark.parametrize("ticket", ["", "x" * 2049])
def test_empty_or_oversized_ticket(client, redis, ticket):
    assert_rejected(exchange(client, ticket), redis)


def test_non_ascii_ticket(client, redis, managed_user):
    assert_rejected(exchange(client, make_ticket() + "é"), redis)


def test_extra_body_fields_rejected(client, redis, managed_user):
    response = client.post("/auth/jrc-exchange", json={"ticket": make_ticket(), "aud": "web"})
    assert_rejected(response, redis)


# Review F5-B02: FastAPI's 422 would echo the rejected input, i.e. a valid unspent ticket.
@pytest.mark.parametrize("shape", [
    lambda t: {"ticket": [t]},
    lambda t: {"ticket": {"value": t}},
    lambda t: {"ticket": t, "ticket2": t},
    lambda t: {"tickets": t},
    lambda t: [t],
    lambda t: t,
    lambda t: {"ticket": [t] * 3},
    lambda t: {"ticket": None, "x": t},
])
def test_malformed_body_never_echoes_ticket(client, redis, managed_user, shape):
    ticket = make_ticket()
    response = client.post("/auth/jrc-exchange", json=shape(ticket))
    assert_rejected(response, redis)
    assert ticket not in response.text
    assert ticket.split(".")[1] not in response.text and ticket.split(".")[2] not in response.text
    # Not spent: the ticket still works once in the correct shape.
    assert exchange(client, ticket).status_code == 200


@pytest.mark.parametrize("content, content_type", [
    (lambda t: t, "text/plain"),
    (lambda t: f"ticket={t}", "application/x-www-form-urlencoded"),
    (lambda t: '{"ticket": "' + t + '"', "application/json"),
    (lambda t: '{"ticket": "' + t + '", "pad": "' + "x" * 3000 + '"}', "application/json"),
    (lambda t: "[" * 5000 + "]" * 5000, "application/json"),
    (lambda t: b"\xff\xfe" + t.encode(), "application/json"),
    (lambda t: "", "application/json"),
])
def test_raw_malformed_bodies_never_echo_ticket(client, redis, managed_user, content, content_type):
    ticket = make_ticket()
    response = client.post("/auth/jrc-exchange", content=content(ticket), headers={"Content-Type": content_type})
    assert_rejected(response, redis)
    assert ticket not in response.text


def test_every_response_is_no_store(client, redis, managed_user, monkeypatch):
    assert exchange(client, make_ticket()).headers["cache-control"] == "no-store"
    monkeypatch.setattr("apps.api.routers.jrc_auth.get_redis", BrokenRedis)
    assert exchange(client, make_ticket()).headers["cache-control"] == "no-store"
    monkeypatch.setenv("JRC_FF_SSO_SECRET", "")
    assert exchange(client, make_ticket()).headers["cache-control"] == "no-store"


@pytest.mark.parametrize("overrides", [
    {"v": 2},
    {"v": True},
    {"iss": "someone-else"},
    {"aud": "admin"},
    {"aud": None},
    {"nonce": None},
    {"nonce": "short"},
    {"nonce": "x" * 65},
    {"nonce": "bad nonce with spaces!!!"},
    {"acct": "not-a-uuid"},
    {"acct": None},
    {"sub": None},
    {"role": "superadmin"},
    {"is_superadmin": True},
])
def test_invalid_claims(client, redis, managed_user, overrides):
    assert_rejected(exchange(client, make_ticket(**overrides)), redis)


# --- account binding ----------------------------------------------------------------


def test_ticket_for_another_account_subject(client, redis, managed_user):
    # Signed, but the FreeFrame id does not belong to the Brightbean account in the ticket.
    other_sub = str(remote_id("account", OTHER_ACCOUNT))
    assert_rejected(exchange(client, make_ticket(sub=other_sub)), redis)


def test_ticket_cannot_target_unmanaged_account(client, redis, managed_user):
    # E.g. the service superadmin or any account created outside JR Content (random id).
    assert_rejected(exchange(client, make_ticket(sub=str(uuid.uuid4()))), redis)


def test_subject_must_be_canonical_uuid(client, redis, managed_user):
    sub = str(remote_id("account", ACCOUNT)).upper()
    assert_rejected(exchange(client, make_ticket(sub=sub)), redis)


def test_nonexistent_account(client, redis, mock_db):
    mock_db.first.return_value = None
    assert_rejected(exchange(client, make_ticket()), redis, consumed=True)


@pytest.mark.parametrize("status", [
    UserStatus.deactivated, UserStatus.pending_invite, UserStatus.pending_verification,
])
def test_inactive_account(client, redis, managed_user, status):
    managed_user.status = status
    assert_rejected(exchange(client, make_ticket()), redis, consumed=True)


def test_deleted_account(client, redis, managed_user):
    managed_user.deleted_at = object()
    assert_rejected(exchange(client, make_ticket()), redis, consumed=True)


def test_superadmin_rejected(client, redis, managed_user):
    managed_user.is_superadmin = True
    assert_rejected(exchange(client, make_ticket()), redis, consumed=True)


def test_service_account_email_rejected(client, redis, managed_user):
    managed_user.email = "JRC-SYNC@example.com"
    assert_rejected(exchange(client, make_ticket()), redis, consumed=True)


# --- audience / target -----------------------------------------------------------------


def test_server_ticket_refused_through_public_entrance(client, redis, managed_user):
    assert_rejected(exchange(client, make_ticket(aud="api"), headers={"X-Real-IP": "127.0.0.1"}), redis)


def test_web_ticket_through_public_entrance(client, redis, managed_user):
    response = exchange(client, make_ticket(), headers={"X-Real-IP": "127.0.0.1"})
    assert response.status_code == 200


def test_server_ticket_cannot_carry_next(client, redis, managed_user):
    assert_rejected(exchange(client, make_ticket(aud="api", next="/projects")), redis)


@pytest.mark.parametrize("next_path", [
    "//evil.example/x", "/\\evil.example", "https://evil.example/", "javascript:alert(1)",
    "projects/x", "/a b", "/a\nb", "/a\\b", "", "/" + "a" * 512, 7,
])
def test_unsafe_next_rejected(client, redis, managed_user, next_path):
    assert_rejected(exchange(client, make_ticket(next=next_path)), redis)


# --- configuration -------------------------------------------------------------------


@pytest.mark.parametrize("secret", ["", "short-secret"])
def test_missing_or_short_secret_disables_exchange(client, redis, monkeypatch, managed_user, secret):
    monkeypatch.setenv("JRC_FF_SSO_SECRET", secret)
    response = exchange(client, make_ticket(secret=secret or "x"))
    assert response.status_code == 503
    assert redis.calls == []


# --- shared format with Brightbean ---------------------------------------------------------


KNOWN_ANSWER_SECRET = b"jrc-known-answer-test-secret-0123456789abcdef"
KNOWN_ANSWER_PAYLOAD = (
    '{"acct":"f5b02000-0000-4000-8000-000000000101","aud":"web","exp":1800000060,'
    '"iat":1800000000,"iss":"jrc-brightbean","nonce":"AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",'
    '"sub":"%s","v":1}'
)
KNOWN_ANSWER_TICKET = (
    "v1.eyJhY2N0IjoiZjViMDIwMDAtMDAwMC00MDAwLTgwMDAtMDAwMDAwMDAwMTAxIiwiYXVkIjoid2ViIiwiZXhwIjoxODAwMDAwM"
    "DYwLCJpYXQiOjE4MDAwMDAwMDAsImlzcyI6ImpyYy1icmlnaHRiZWFuIiwibm9uY2UiOiJBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQ"
    "UFBQUFBQUFBQSIsInN1YiI6IjRjNDQ0MTcxLTRmZGQtNTc5NC1iZDc1LTFhODkyZGQ2MGE4NCIsInYiOjF9.6lJYv6_vv7WmwmLW"
    "sbBMfcewDrTBiYL2Ntlci3me-GU"
)


def test_known_answer_vector_shared_with_brightbean():
    payload = KNOWN_ANSWER_PAYLOAD % remote_id("account", ACCOUNT)
    part = b64(payload.encode())
    assert f"v1.{part}.{signature(part, KNOWN_ANSWER_SECRET)}" == KNOWN_ANSWER_TICKET
    claims = verify_ticket(KNOWN_ANSWER_TICKET, secret=KNOWN_ANSWER_SECRET, now=1800000030)
    assert claims["acct"] == str(ACCOUNT)
    with pytest.raises(jrc_ticket.TicketError):
        verify_ticket(KNOWN_ANSWER_TICKET, secret=KNOWN_ANSWER_SECRET, now=1800000060)


# --- real Redis (SET NX) ------------------------------------------------------------------


def test_consume_nonce_against_real_redis():
    redis_lib = pytest.importorskip("redis")
    client = redis_lib.from_url(os.environ["REDIS_URL"], socket_connect_timeout=1, decode_responses=True)
    try:
        client.ping()
    except Exception:
        pytest.skip("Redis not reachable")
    nonce = uuid.uuid4().hex
    try:
        assert consume_nonce(client, nonce) is True
        assert consume_nonce(client, nonce) is False
        key = jrc_ticket.NONCE_PREFIX + __import__("hashlib").sha256(nonce.encode()).hexdigest()
        assert 0 < client.ttl(key) <= 120
    finally:
        client.delete(jrc_ticket.NONCE_PREFIX + __import__("hashlib").sha256(nonce.encode()).hexdigest())
