"""Security regression: HLS bypasses the global /stream/ rate limiter."""
import asyncio
import uuid
import redis
from unittest.mock import patch

import pytest
from botocore.exceptions import ConnectTimeoutError, ReadTimeoutError
from fastapi import HTTPException, Request

from apps.api.config import settings
from apps.api.routers import hls_proxy as hls
from apps.api.services.hls_limits import HlsLimits
from .hls_helpers import hls_url, segment_storage


@pytest.fixture(autouse=True)
def shared_hls_redis(monkeypatch):
    connection = redis.Redis.from_url(settings.redis_url, decode_responses=True)
    namespace = f"test-hls:{uuid.uuid4().hex}"
    monkeypatch.setattr("apps.api.services.hls_limits.get_redis", lambda: connection)
    monkeypatch.setattr(HlsLimits, "__init__", lambda self: setattr(self, "namespace", namespace))
    yield connection
    keys = list(connection.scan_iter(namespace + '*'))
    if keys:
        connection.delete(*keys)


def active_total(limits):
    from apps.api.services.hls_limits import get_redis
    return get_redis().zcard(limits.namespace + ':active:total')


@pytest.fixture
def managed_stream(client, test_user, monkeypatch):
    limits = HlsLimits()
    monkeypatch.setattr(hls, "hls_limits", limits)
    token = hls.create_hls_token("processed/v1", managed=True, user_id=str(test_user.id),
                                 project_id=str(uuid.uuid4()), token_version=1)
    url = hls_url(client, token, "720p/seg.ts")
    with (patch.object(hls, "get_user_by_id", return_value=test_user),
          patch.object(hls, "require_project_role")):
        yield token, url, limits


@pytest.mark.parametrize("budget", ["user", "total"])
def test_hls_request_rate_rejects_before_reading_storage(client, managed_stream, monkeypatch, budget):
    _, url, limits = managed_stream
    monkeypatch.setattr(settings, f"hls_requests_per_minute_{budget}", 2)
    with segment_storage() as (storage, body):
        body.iter_chunks.side_effect = lambda **kwargs: iter([b"segment"])
        for _ in range(2):
            assert client.get(url).status_code == 200
        response = client.get(url)
        assert response.status_code == 429
        assert response.headers["retry-after"] == "60"
        assert storage.get_object.call_count == 2
        assert active_total(limits) == 0


@pytest.mark.parametrize("budget", ["user", "total"])
def test_open_stream_occupies_capacity_until_response_finishes(client, mock_db, managed_stream, monkeypatch, budget):
    token, _, limits = managed_stream
    monkeypatch.setattr(settings, f"hls_concurrent_streams_{budget}", 1)
    request = Request({"type": "http", "scheme": "http", "path": "/", "headers": [
        (b"cookie", f"ff_hls={token}".encode())], "client": ("testclient", 1000)})
    with segment_storage() as (storage, body):
        response = hls.browser_hls_proxy(hls.hls_session_id("processed/v1"), "720p/seg.ts", request, mock_db)
        assert active_total(limits) == 1
        with pytest.raises(HTTPException) as denied:
            hls.browser_hls_proxy(hls.hls_session_id("processed/v1"), "720p/seg.ts", request, mock_db)
        assert denied.value.status_code == 429
        assert storage.get_object.call_count == 1

        async def disconnected():
            return {"type": "http.disconnect"}

        async def send(message):
            pass

        asyncio.run(response({"type": "http", "asgi": {"spec_version": "2.0"}}, disconnected, send))
        assert active_total(limits) == 0
        body.close.assert_called()


def test_rate_budget_survives_release_and_shared_workers(monkeypatch, shared_hls_redis):
    first, second = HlsLimits(), HlsLimits()
    monkeypatch.setattr(settings, "hls_requests_per_minute_user", 1)
    release = first.acquire("user:a")
    release()
    release()
    with pytest.raises(HTTPException):
        second.acquire("user:a")
    second.acquire("user:b")()
    for key in shared_hls_redis.scan_iter(first.namespace + ':rate:*'):
        shared_hls_redis.delete(key)
    second.acquire("user:a")()
    assert active_total(first) == 0


def test_total_capacity_and_safe_release_after_expiry(monkeypatch, shared_hls_redis):
    first, second = HlsLimits(), HlsLimits()
    monkeypatch.setattr(settings, "hls_concurrent_streams_total", 1)
    old_release = first.acquire("user:a")
    with pytest.raises(HTTPException):
        second.acquire("user:b")
    # Simulate the worker dying and Redis expiring its lease.
    for key in shared_hls_redis.scan_iter(first.namespace + ':active:*'):
        shared_hls_redis.zadd(key, {member: 0 for member in shared_hls_redis.zrange(key, 0, -1)})
    release = second.acquire("user:b")
    old_release()
    assert active_total(first) == 1
    release()
    release()
    assert active_total(first) == 0


def test_same_user_shares_budget_across_different_hls_tokens(client, test_user, mock_db, managed_stream, monkeypatch):
    _, url, _ = managed_stream
    monkeypatch.setattr(settings, "hls_requests_per_minute_user", 1)
    with segment_storage() as (storage, body):
        assert client.get(url).status_code == 200
        token = hls.create_hls_token("processed/other", managed=True, user_id=str(test_user.id),
                                     project_id=str(uuid.uuid4()), token_version=1)
        url = hls_url(client, token, "720p/seg.ts")
        assert client.get(url).status_code == 429
        assert storage.get_object.call_count == 1


@pytest.mark.parametrize("error", [ConnectTimeoutError, ReadTimeoutError])
def test_s3_timeout_returns_generic_504_and_releases_capacity(client, managed_stream, error):
    _, url, limits = managed_stream
    with patch.object(hls, "_stream_s3_client") as storage:
        storage.return_value.get_object.side_effect = error(endpoint_url="private-storage")
        response = client.get(url)
    assert response.status_code == 504
    assert "private-storage" not in response.text
    assert active_total(limits) == 0


def test_stream_client_uses_bounded_timeouts_without_retries():
    hls._stream_s3_client.cache_clear()
    try:
        with patch.object(hls, "_build_s3_client") as build:
            hls._stream_s3_client()
            config = build.call_args.args[0]
            assert config.connect_timeout == settings.hls_s3_connect_timeout_seconds
            assert config.read_timeout == settings.hls_s3_read_timeout_seconds
            assert config.retries == {"total_max_attempts": 1}
    finally:
        hls._stream_s3_client.cache_clear()


def test_oversize_segment_is_closed_and_rejected_before_streaming(client, managed_stream):
    _, url, limits = managed_stream
    with segment_storage() as (storage, body):
        storage.get_object.return_value["ContentLength"] = settings.hls_max_segment_bytes + 1
        response = client.get(url)
    assert response.status_code == 413
    body.iter_chunks.assert_not_called()
    body.close.assert_called()
    assert active_total(limits) == 0


@pytest.mark.parametrize("failure", ["size", "time", "read_timeout", "disconnect"])
def test_midstream_failure_closes_storage_and_releases_capacity(client, managed_stream, monkeypatch, failure):
    _, url, limits = managed_stream
    with segment_storage() as (storage, body):
        if failure == "size":
            monkeypatch.setattr(settings, "hls_max_segment_bytes", 4)
            storage.get_object.return_value["ContentLength"] = 0  # do not trust metadata alone
            body.iter_chunks.return_value = iter([b"one", b"overflow"])
        elif failure == "time":
            monkeypatch.setattr(settings, "hls_stream_timeout_seconds", 0.001)
            def slow_chunks(**kwargs):
                import time
                time.sleep(0.02)
                yield b"late"
            body.iter_chunks.side_effect = slow_chunks
        elif failure == "read_timeout":
            body.iter_chunks.side_effect = ReadTimeoutError(endpoint_url="private-storage")
        else:
            def interrupted(**kwargs):
                yield b"start"
                raise ConnectionError("disconnected")
            body.iter_chunks.side_effect = interrupted
        client.get(url)
    body.close.assert_called()
    assert active_total(limits) == 0
