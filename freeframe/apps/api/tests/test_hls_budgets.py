"""Security regression: HLS bypasses the global /stream/ rate limiter."""
import asyncio
import uuid
from unittest.mock import patch

import pytest
from botocore.exceptions import ConnectTimeoutError, ReadTimeoutError
from fastapi import HTTPException, Request

from apps.api.config import settings
from apps.api.routers import hls_proxy as hls
from apps.api.services.hls_limits import HlsLimits
from .hls_helpers import hls_url, segment_storage


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


@pytest.mark.parametrize("budget", ["ip", "session"])
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
        assert limits.total == 0


@pytest.mark.parametrize("budget", ["ip", "session", "total"])
def test_open_stream_occupies_capacity_until_response_finishes(client, mock_db, managed_stream, monkeypatch, budget):
    token, _, limits = managed_stream
    monkeypatch.setattr(settings, f"hls_concurrent_streams_{budget}", 1)
    request = Request({"type": "http", "scheme": "http", "path": "/", "headers": [
        (b"cookie", f"ff_hls={token}".encode())], "client": ("testclient", 1000)})
    with segment_storage() as (storage, body):
        response = hls.browser_hls_proxy(hls.hls_session_id("processed/v1"), "720p/seg.ts", request, mock_db)
        assert limits.total == 1
        with pytest.raises(HTTPException) as denied:
            hls.browser_hls_proxy(hls.hls_session_id("processed/v1"), "720p/seg.ts", request, mock_db)
        assert denied.value.status_code == 429
        assert storage.get_object.call_count == 1

        async def disconnected():
            return {"type": "http.disconnect"}

        async def send(message):
            pass

        asyncio.run(response({"type": "http", "asgi": {"spec_version": "2.0"}}, disconnected, send))
        assert limits.total == 0
        assert not limits.active
        body.close.assert_called()


def test_rate_budget_is_not_reset_by_finishing_request_and_expires(monkeypatch):
    limits = HlsLimits()
    monkeypatch.setattr(settings, "hls_requests_per_minute_ip", 1)
    with patch("apps.api.services.hls_limits.monotonic", return_value=10):
        release = limits.acquire("ip", "s1")
        release()
        release()  # cleanup must be safe to repeat
        with pytest.raises(HTTPException):
            limits.acquire("ip", "s2")
    with patch("apps.api.services.hls_limits.monotonic", return_value=71):
        limits.acquire("ip", "s2")()
    assert limits.total == 0


@pytest.mark.parametrize("error", [ConnectTimeoutError, ReadTimeoutError])
def test_s3_timeout_returns_generic_504_and_releases_capacity(client, managed_stream, error):
    _, url, limits = managed_stream
    with patch.object(hls, "_stream_s3_client") as storage:
        storage.return_value.get_object.side_effect = error(endpoint_url="private-storage")
        response = client.get(url)
    assert response.status_code == 504
    assert "private-storage" not in response.text
    assert limits.total == 0


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
    assert limits.total == 0


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
    assert limits.total == 0
    assert not limits.active
