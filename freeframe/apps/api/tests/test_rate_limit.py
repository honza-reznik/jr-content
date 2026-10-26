"""Review P1: budgets isolate accounts/users and are atomic between workers."""
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import MagicMock
import uuid

import pytest
import redis
from fastapi import HTTPException
from apps.api.config import settings
from apps.api.services import redis_service
from apps.api.middleware.rate_limit import enforce_user_limit


@pytest.fixture
def shared_redis(monkeypatch):
    connection = redis.Redis.from_url(settings.redis_url, decode_responses=True)
    prefix = f"test-rate:{uuid.uuid4().hex}:"
    monkeypatch.setattr(redis_service, "get_redis", lambda: connection)
    monkeypatch.setattr(redis_service, "RATE_LIMIT_PREFIX", prefix)
    yield connection
    keys = list(connection.scan_iter(prefix + '*'))
    if keys:
        connection.delete(*keys)


def test_atomic_budget_across_connections(shared_redis):
    def attempt(_):
        return redis_service.check_rate_limit('total', 'parallel', 7, 60, fail_closed=True)[0]
    with ThreadPoolExecutor(max_workers=16) as pool:
        assert sum(pool.map(attempt, range(40))) == 7


def test_user_and_total_budgets_are_independent_of_ip(shared_redis):
    a, b = SimpleNamespace(id='a'), SimpleNamespace(id='b')
    for _ in range(2):
        enforce_user_limit(a, 'upload', 2, 60)
    with pytest.raises(HTTPException) as error:
        enforce_user_limit(a, 'upload', 2, 60)
    assert error.value.status_code == 429
    enforce_user_limit(b, 'upload', 2, 60)
    shared_redis.set(redis_service.RATE_LIMIT_PREFIX + 'upload:total', 200, ex=60)
    with pytest.raises(HTTPException):
        enforce_user_limit(SimpleNamespace(id='c'), 'upload', 2, 60)


def test_both_upload_routes_share_verified_user_budget(client, auth_headers, test_user, shared_redis):
    from apps.api.main import app
    from apps.api.middleware.auth import get_current_user
    body = dict(project_id=str(uuid.uuid4()), asset_name='test', original_filename='file.txt',
                mime_type='text/plain', file_size_bytes=1)
    for _ in range(5):
        assert client.post('/upload/initiate', json=body, headers=auth_headers).status_code == 400
    assert client.post(f'/assets/{uuid.uuid4()}/versions', json=body, headers=auth_headers).status_code == 429
    other = MagicMock(id=uuid.uuid4())
    app.dependency_overrides[get_current_user] = lambda: other
    assert client.post('/upload/initiate', json=body, headers=auth_headers).status_code == 400


@pytest.mark.parametrize('path,maximum,body', [
    ('login', 10, {'password':'wrong'}),
    ('send-magic-code', 5, {}),
    ('verify-magic-code', 10, {'code':'123456'}),
])
def test_account_limit_normalizes_email_without_blocking_same_ip(client, shared_redis, path, maximum, body):
    for _ in range(maximum):
        assert client.post('/auth/' + path, json={'email':'one@example.com', **body}).status_code in (200,401)
    assert client.post('/auth/' + path, json={'email':'ONE@example.com', **body}).status_code == 429
    assert client.post('/auth/' + path, json={'email':'two@example.com', **body}).status_code in (200,401)
    shared_redis.set(redis_service.RATE_LIMIT_PREFIX + path.replace('-', '_') + ':total', maximum * 100, ex=60)
    assert client.post('/auth/' + path, json={'email':'three@example.com', **body}).status_code == 429


@pytest.mark.parametrize('path,maximum,body', [
    ('login', 10, {'password': 'wrong'}),
    ('send-magic-code', 5, {}),
    ('verify-magic-code', 10, {'code': '123456'}),
])
def test_display_names_share_the_authenticated_email_budget(client, shared_redis, path, maximum, body, monkeypatch):
    from apps.api.routers import auth
    lookup = MagicMock(return_value=None)
    monkeypatch.setattr(auth, 'get_user_by_email', lookup)
    for attempt in range(maximum):
        email = f'Pokus{attempt + 1} <victim@EXAMPLE.com>'
        assert client.post('/auth/' + path, json={'email': email, **body}).status_code in (200, 401)
        assert lookup.call_args.args[1] == 'victim@example.com'
    for email in ['victim@example.com', 'Pokus999 <victim@example.com>', ' Victim <victim@EXAMPLE.com> ']:
        assert client.post('/auth/' + path, json={'email': email, **body}).status_code == 429
    assert lookup.call_count == maximum
    assert client.post('/auth/' + path, json={'email': 'other@example.com', **body}).status_code in (200, 401)
    assert client.post('/auth/' + path, json={'email': 'invalid', **body}).status_code == 422


def test_refresh_budget_uses_verified_user_not_shared_ip(client, shared_redis, monkeypatch):
    from apps.api.routers import auth
    from apps.api.services.auth_service import create_refresh_token
    from .conftest import _make_user
    first, second = _make_user(), _make_user()
    users = {first.id: first, second.id: second}
    monkeypatch.setattr(auth, 'get_user_by_id', lambda db, uid: users.get(uid))
    def refresh(user):
        return client.post('/auth/refresh', json={'refresh_token': create_refresh_token(str(user.id))})
    for _ in range(30):
        assert refresh(first).status_code == 200
    assert refresh(first).status_code == 429
    assert refresh(second).status_code == 200
