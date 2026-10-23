"""F5-B02: tickets for FreeFrame sign-in and server calls on behalf of a user."""

import base64
import hashlib
import hmac
import json
import time
import uuid
from unittest.mock import Mock, patch

import pytest
from django.utils import timezone

from apps.jrc_video.client import _USER_TOKENS, FreeFrameError, FreeFrameUserClient
from apps.jrc_video.models import VideoAccount
from apps.jrc_video.tickets import TicketError, encode_ticket, issue_ticket

pytestmark = pytest.mark.django_db
SECRET = "unit-test-only-sso-secret-0123456789abcdef"


@pytest.fixture(autouse=True)
def empty_user_tokens():
    _USER_TOKENS.clear()
    yield
    _USER_TOKENS.clear()


@pytest.fixture
def account(user):
    return VideoAccount.objects.create(user=user, ff_user_id=uuid.uuid4(), synced_at=timezone.now())


def decode(ticket, secret=SECRET):
    version, payload, signature = ticket.split(".")
    assert version == "v1"
    expected = hmac.new(secret.encode(), b"jrc-ff-ticket.v1." + payload.encode(), hashlib.sha256).digest()
    assert base64.urlsafe_b64decode(signature + "=" * (-len(signature) % 4)) == expected
    return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))


def response(status=200, body=None):
    result = Mock(status_code=status)
    result.json.return_value = body
    return result


def exchange_ok(token="ff-access-token", expires_in=900):
    return response(200, {"access_token": token, "token_type": "bearer", "expires_in": expires_in})


# --- issuing -----------------------------------------------------------------------


def test_ticket_is_signed_short_lived_and_bound_to_account(user, account):
    before = int(time.time())
    claims = decode(issue_ticket(user, audience="web", next_path="/projects/p/assets/a?embed=1"))
    assert claims["v"] == 1 and claims["iss"] == "jrc-brightbean" and claims["aud"] == "web"
    assert claims["acct"] == str(user.pk)
    assert claims["sub"] == str(account.ff_user_id)
    assert claims["exp"] - claims["iat"] == 60 and claims["iat"] >= before
    assert claims["next"] == "/projects/p/assets/a?embed=1"
    assert len(claims["nonce"]) >= 32


def test_every_ticket_has_a_new_nonce(user, account):
    nonces = {decode(issue_ticket(user, audience="api"))["nonce"] for _ in range(20)}
    assert len(nonces) == 20


def test_api_ticket_has_no_next(user, account):
    assert "next" not in decode(issue_ticket(user, audience="api"))
    with pytest.raises(ValueError):
        issue_ticket(user, audience="api", next_path="/projects")


@pytest.mark.parametrize("audience", ["admin", "", None, "WEB"])
def test_unknown_audience(user, account, audience):
    with pytest.raises(ValueError):
        issue_ticket(user, audience=audience)


@pytest.mark.parametrize(
    "next_path", ["//evil.example", "/\\evil", "https://evil.example", "projects", "/a b", "/a\nb", "", "/" + "a" * 512]
)
def test_unsafe_next_is_refused(user, account, next_path):
    with pytest.raises(ValueError):
        issue_ticket(user, audience="web", next_path=next_path)


def test_inactive_user_gets_no_ticket(user, account):
    user.is_active = False
    user.save(update_fields=["is_active"])
    with pytest.raises(TicketError):
        issue_ticket(user, audience="web")


def test_stale_user_object_cannot_bypass_block(user, account):
    type(user).objects.filter(pk=user.pk).update(is_active=False)
    assert user.is_active  # stale in-memory object
    with pytest.raises(TicketError):
        issue_ticket(user, audience="api")


def test_deleted_user_gets_no_ticket(user, account):
    stale = type(user).objects.get(pk=user.pk)
    user.delete()
    with pytest.raises(TicketError):
        issue_ticket(stale, audience="web")


def test_unsynchronized_user_gets_no_ticket(user):
    with pytest.raises(TicketError, match="synchronize"):
        issue_ticket(user, audience="web")


@pytest.mark.parametrize("secret", ["", "too-short", None])
def test_missing_or_short_secret(user, account, settings, secret):
    settings.JRC_FF_SSO_SECRET = secret
    with pytest.raises(TicketError, match="JRC_FF_SSO_SECRET"):
        issue_ticket(user, audience="web")


def test_ticket_is_not_signed_with_sync_password(user, account, settings):
    ticket = issue_ticket(user, audience="web")
    with pytest.raises(AssertionError):
        decode(ticket, secret=settings.JRC_FF_SYNC_PASSWORD)


# Same vector as freeframe/apps/api/tests/test_jrc_exchange.py (shared format).
KNOWN_ANSWER_SECRET = b"jrc-known-answer-test-secret-0123456789abcdef"
KNOWN_ANSWER_TICKET = (
    "v1.eyJhY2N0IjoiZjViMDIwMDAtMDAwMC00MDAwLTgwMDAtMDAwMDAwMDAwMTAxIiwiYXVkIjoid2ViIiwiZXhwIjoxODAwMDAwM"
    "DYwLCJpYXQiOjE4MDAwMDAwMDAsImlzcyI6ImpyYy1icmlnaHRiZWFuIiwibm9uY2UiOiJBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQ"
    "UFBQUFBQUFBQSIsInN1YiI6IjRjNDQ0MTcxLTRmZGQtNTc5NC1iZDc1LTFhODkyZGQ2MGE4NCIsInYiOjF9.6lJYv6_vv7WmwmLW"
    "sbBMfcewDrTBiYL2Ntlci3me-GU"
)


def test_known_answer_vector_shared_with_freeframe():
    claims = {
        "acct": "f5b02000-0000-4000-8000-000000000101",
        "aud": "web",
        "exp": 1800000060,
        "iat": 1800000000,
        "iss": "jrc-brightbean",
        "nonce": "A" * 32,
        "sub": str(
            uuid.uuid5(
                uuid.UUID("29c4ee9e-ab19-48c4-bb44-2fc77109b234"), "account:f5b02000-0000-4000-8000-000000000101"
            )
        ),
        "v": 1,
    }
    assert encode_ticket(claims, KNOWN_ANSWER_SECRET) == KNOWN_ANSWER_TICKET


# --- calls on behalf of the user (AK3) ------------------------------------------------------


def test_user_client_exchanges_directly_and_uses_bearer(user, account):
    with patch(
        "apps.jrc_video.client.requests.request", side_effect=[exchange_ok(), response(200, {"id": "me"})]
    ) as send:
        assert FreeFrameUserClient(user).request("GET", "/auth/me") == {"id": "me"}
    exchange_call, api_call = send.call_args_list
    assert exchange_call.args == ("POST", "http://jrc-ff-api:8000/auth/jrc-exchange")
    assert exchange_call.kwargs["headers"] == {}
    assert exchange_call.kwargs["allow_redirects"] is False
    assert exchange_call.kwargs["proxies"] == {"http": "", "https": ""}
    claims = decode(exchange_call.kwargs["json"]["ticket"])
    assert claims["aud"] == "api" and claims["sub"] == str(account.ff_user_id)
    assert api_call.args == ("GET", "http://jrc-ff-api:8000/auth/me")
    assert api_call.kwargs["headers"] == {"Authorization": "Bearer ff-access-token"}


def test_user_token_is_reused_until_close_to_expiry(user, account):
    with patch(
        "apps.jrc_video.client.requests.request",
        side_effect=[exchange_ok(), response(200, {}), response(200, {})],
    ) as send:
        FreeFrameUserClient(user).request("GET", "/auth/me")
        FreeFrameUserClient(user).request("GET", "/auth/me")
    assert [call.args[1].rsplit("/", 1)[-1] for call in send.call_args_list] == ["jrc-exchange", "me", "me"]


def test_short_lived_token_is_not_cached(user, account):
    with patch(
        "apps.jrc_video.client.requests.request",
        side_effect=[exchange_ok(expires_in=60), response(200, {}), exchange_ok(expires_in=60), response(200, {})],
    ) as send:
        FreeFrameUserClient(user).request("GET", "/auth/me")
        FreeFrameUserClient(user).request("GET", "/auth/me")
    assert send.call_count == 4


def test_tokens_are_separate_per_user(user, account, organization):
    other = type(user).objects.create_user(email="other@example.com", password="x" * 12, name="Other")
    VideoAccount.objects.create(user=other, ff_user_id=uuid.uuid4(), synced_at=timezone.now())
    with patch(
        "apps.jrc_video.client.requests.request",
        side_effect=[exchange_ok("token-a"), response(200, {}), exchange_ok("token-b"), response(200, {})],
    ) as send:
        FreeFrameUserClient(user).request("GET", "/auth/me")
        FreeFrameUserClient(other).request("GET", "/auth/me")
    assert send.call_args_list[3].kwargs["headers"] == {"Authorization": "Bearer token-b"}


def test_expired_token_gets_one_new_ticket(user, account):
    with patch(
        "apps.jrc_video.client.requests.request",
        side_effect=[exchange_ok("old"), response(401), exchange_ok("new"), response(200, {"ok": True})],
    ) as send:
        assert FreeFrameUserClient(user).request("GET", "/auth/me") == {"ok": True}
    first, second = (decode(call.kwargs["json"]["ticket"])["nonce"] for call in send.call_args_list[::2])
    assert first != second
    assert send.call_args_list[3].kwargs["headers"] == {"Authorization": "Bearer new"}


def test_rejected_exchange_raises_without_retry_loop(user, account):
    with (
        patch("apps.jrc_video.client.requests.request", return_value=response(401)) as send,
        pytest.raises(FreeFrameError) as error,
    ):
        FreeFrameUserClient(user).request("GET", "/auth/me")
    assert error.value.status_code == 401
    assert send.call_count == 1
    assert not _USER_TOKENS


def test_blocked_user_is_refused_before_any_network_call(user, account):
    with patch("apps.jrc_video.client.requests.request", side_effect=[exchange_ok(), response(200, {})]):
        FreeFrameUserClient(user).request("GET", "/auth/me")
    assert _USER_TOKENS
    type(user).objects.filter(pk=user.pk).update(is_active=False)
    with patch("apps.jrc_video.client.requests.request") as send, pytest.raises(FreeFrameError, match="inactive"):
        FreeFrameUserClient(user).request("GET", "/auth/me")
    send.assert_not_called()
    assert not _USER_TOKENS


def test_unsynchronized_user_is_refused_without_network(user):
    with patch("apps.jrc_video.client.requests.request") as send, pytest.raises(FreeFrameError, match="synchronize"):
        FreeFrameUserClient(user).request("GET", "/auth/me")
    send.assert_not_called()


@pytest.mark.parametrize(
    "body",
    [
        None,
        [],
        {"access_token": ""},
        {"access_token": "x"},
        {"access_token": "x", "expires_in": True},
        {"access_token": "x", "expires_in": 0},
        {"access_token": 5, "expires_in": 900},
        # A server ticket must never return a long-lived refresh token.
        {"access_token": "x", "expires_in": 900, "refresh_token": "y"},
    ],
)
def test_invalid_exchange_response(user, account, body):
    with (
        patch("apps.jrc_video.client.requests.request", return_value=response(200, body)),
        pytest.raises(FreeFrameError, match="invalid ticket exchange"),
    ):
        FreeFrameUserClient(user).request("GET", "/auth/me")
    assert not _USER_TOKENS


@pytest.mark.parametrize("path", ["@evil.example/x", "evil", "", None])
def test_path_cannot_leave_internal_api(user, account, path):
    with patch("apps.jrc_video.client.requests.request") as send, pytest.raises(FreeFrameError, match="path"):
        FreeFrameUserClient(user).request("GET", path)
    send.assert_not_called()


def test_ticket_and_token_never_in_errors_or_logs(user, account, caplog):
    with (
        patch(
            "apps.jrc_video.client.requests.request", side_effect=[exchange_ok("secret-token"), response(500)]
        ) as send,
        pytest.raises(FreeFrameError) as error,
    ):
        FreeFrameUserClient(user).request("GET", "/auth/me")
    ticket = send.call_args_list[0].kwargs["json"]["ticket"]
    assert ticket not in str(error.value) and "secret-token" not in str(error.value)
    assert ticket not in caplog.text and "secret-token" not in caplog.text
