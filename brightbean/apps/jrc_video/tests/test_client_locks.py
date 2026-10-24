"""F5-B05 AK1: the ticket exchange lock is per user."""

import threading
import time
import uuid
from unittest.mock import Mock, patch

import pytest
from django.utils import timezone

from apps.accounts.models import User
from apps.jrc_video.client import _USER_EXCHANGE_LOCKS, _USER_TOKENS, FreeFrameUserClient
from apps.jrc_video.models import VideoAccount

pytestmark = pytest.mark.django_db
SECRET = "unit-test-only-sso-secret-0123456789abcdef"


@pytest.fixture(autouse=True)
def clean(settings):
    settings.JRC_FF_SSO_SECRET = SECRET
    _USER_TOKENS.clear()
    _USER_EXCHANGE_LOCKS.clear()
    yield
    _USER_TOKENS.clear()
    _USER_EXCHANGE_LOCKS.clear()


def person(email):
    created = User.objects.create_user(email=email, password="x", tos_accepted_at=timezone.now())
    VideoAccount.objects.create(user=created, ff_user_id=uuid.uuid4(), synced_at=timezone.now())
    return created


def reply(token):
    result = Mock(status_code=200)
    result.json.return_value = {"access_token": token, "token_type": "bearer", "expires_in": 900}
    return result


@pytest.mark.django_db(transaction=True)
def test_slow_exchange_of_one_user_does_not_block_another():
    first, second = person("a@example.com"), person("b@example.com")
    release = threading.Event()
    started = threading.Event()
    finished = {}

    def fake(method, url, **kwargs):
        if kwargs["json"]["ticket"] and not started.is_set():
            started.set()
            assert release.wait(5)
        return reply("token-" + str(len(finished)))

    def slow_user():
        FreeFrameUserClient(first)._authenticate()
        finished["first"] = time.monotonic()

    with patch("apps.jrc_video.client.requests.request", side_effect=fake):
        thread = threading.Thread(target=slow_user)
        thread.start()
        assert started.wait(5)
        began = time.monotonic()
        FreeFrameUserClient(second)._authenticate()  # must not wait for the first user's exchange
        assert time.monotonic() - began < 2
        assert "first" not in finished
        release.set()
        thread.join(5)
    assert "first" in finished


@pytest.mark.django_db(transaction=True)
def test_two_tabs_of_one_user_exchange_the_ticket_once():
    user = person("c@example.com")
    gate = threading.Event()
    calls = []

    def fake(method, url, **kwargs):
        calls.append(url)
        gate.wait(0.3)
        return reply("one-token")

    tokens = []

    def tab():
        client = FreeFrameUserClient(user)
        client._authenticate()
        tokens.append(client.token)

    with patch("apps.jrc_video.client.requests.request", side_effect=fake):
        threads = [threading.Thread(target=tab) for _ in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(10)
    assert len(calls) == 1 and tokens == ["one-token"] * 6


@pytest.mark.django_db(transaction=True)
def test_tokens_stay_separate_per_user():
    first, second = person("d@example.com"), person("e@example.com")
    with patch("apps.jrc_video.client.requests.request", side_effect=[reply("t1"), reply("t2")]):
        one, two = FreeFrameUserClient(first), FreeFrameUserClient(second)
        one._authenticate()
        two._authenticate()
    assert (one.token, two.token) == ("t1", "t2")
    assert len(_USER_TOKENS) == 2
