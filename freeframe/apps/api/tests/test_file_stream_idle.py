"""P2-B05: a file transfer has an idle limit, not a total one, and keeps its capacity lease alive."""
import asyncio
import hashlib
import os
import time
import uuid
from io import BytesIO
from unittest.mock import Mock

import pytest
import redis
from botocore.response import StreamingBody
from starlette.requests import ClientDisconnect

from apps.api.config import settings
from apps.api.routers import files
from apps.api.routers.hls_proxy import _HlsStreamResponse
from apps.api.services import hls_limits as limits_module
from apps.api.services.hls_limits import HlsLimits, Lease, lease_ttl
from .test_jrc_live_membership import live_project, isolate_request_limits  # noqa: F401

PIECE = 64 * 1024


class SlowBody(StreamingBody):
    """A locally generated object that storage hands over one piece at a time, with a pause."""

    def __init__(self, data, pause):
        super().__init__(BytesIO(data), len(data))
        self.pause = pause

    def iter_chunks(self, chunk_size=1024):
        for chunk in super().iter_chunks(chunk_size):
            time.sleep(self.pause)
            yield chunk


def _response(pieces, *, idle, release=None):
    body = Mock()
    response = _HlsStreamResponse(iter(pieces), body=body, media_type="video/mp4")
    response.idle_seconds = idle
    response.release = release or Mock()
    return response, body


# The ASGI server sees the cut as an error after the headers: it closes the connection.
CUT = (TimeoutError, ClientDisconnect)


@pytest.fixture(params=["2.0", "2.4"])
def spec(request):
    return request.param


def _run(response, send, spec="2.4"):
    async def receive():
        await asyncio.sleep(3600)
        return {"type": "http.disconnect"}

    asyncio.run(response({"type": "http", "asgi": {"spec_version": spec}}, receive, send))


def test_transfer_longer_than_many_idle_limits_completes(client, live_project, monkeypatch):
    """The old gateway cut every transfer at a fixed total; now only a stall ends it."""
    live_project.user.jrc_managed = False
    live_project.db.flush()
    data = os.urandom(6 * 1024 * 1024)
    s3 = Mock()
    s3.get_object.side_effect = lambda **params: {"Body": SlowBody(data, 0.01), "ContentLength": len(data),
                                                  "ContentType": "video/quicktime"}
    monkeypatch.setattr(files, "_stream_s3_client", lambda: s3)
    lease = Mock()
    monkeypatch.setattr(files.hls_limits, "acquire", lambda identity: lease)
    monkeypatch.setattr(settings, "file_stream_idle_seconds", 0.25)

    started = time.monotonic()
    response = client.get("/files/" + live_project.media.s3_key_raw, params={"filename": "Original.MOV"},
                          headers=live_project.headers)
    elapsed = time.monotonic() - started

    assert response.status_code == 200
    assert hashlib.sha256(response.content).hexdigest() == hashlib.sha256(data).hexdigest()
    # 96 pieces at 10 ms: several times the idle limit in total, none of them stalled.
    assert elapsed > 4 * settings.file_stream_idle_seconds
    assert lease.renew.call_count == len(data) // PIECE
    lease.assert_called()


def test_client_that_stops_reading_is_cut_and_releases_capacity(spec):
    release = Mock()
    response, body = _response([b"a" * PIECE] * 10, idle=0.1, release=release)
    sent = []

    async def send(message):
        sent.append(message)
        if len(sent) == 3:
            await asyncio.sleep(1)  # the client stops taking data after the second piece

    started = time.monotonic()
    with pytest.raises(CUT):
        _run(response, send, spec)
    assert time.monotonic() - started < 0.9
    assert len(sent) == 3
    body.close.assert_called()
    release.assert_called()


def test_stalled_storage_read_is_cut_and_releases_capacity(spec):
    release = Mock()

    def pieces():
        yield b"first"
        time.sleep(0.4)
        yield b"late"

    response, body = _response(pieces(), idle=0.1, release=release)
    sent = []

    async def send(message):
        sent.append(message)

    with pytest.raises(CUT):
        _run(response, send, spec)
    assert [m.get("body") for m in sent[1:]] == [b"first"]
    body.close.assert_called()
    release.assert_called()


def test_segments_keep_their_total_deadline(monkeypatch):
    monkeypatch.setattr(settings, "hls_stream_timeout_seconds", 0.1)

    def pieces():
        for _ in range(10):
            time.sleep(0.03)
            yield b"segment"

    response, body = _response(pieces(), idle=None)

    async def send(message):
        pass

    with pytest.raises(CUT):
        _run(response, send)
    body.close.assert_called()


@pytest.fixture
def shared_redis(monkeypatch):
    connection = redis.Redis.from_url(settings.redis_url, decode_responses=True)
    namespace = f"test-lease:{uuid.uuid4().hex}"
    monkeypatch.setattr(limits_module, "get_redis", lambda: connection)
    monkeypatch.setattr(HlsLimits, "__init__", lambda self: setattr(self, "namespace", namespace))
    yield connection, namespace
    keys = list(connection.scan_iter(namespace + "*"))
    if keys:
        connection.delete(*keys)


def test_lease_ttl_is_unchanged_and_shared_with_brightbean():
    assert lease_ttl() == 369


def test_renew_moves_a_live_lease_forward_and_never_recreates_an_expired_one(shared_redis):
    connection, namespace = shared_redis
    lease = HlsLimits().acquire("user:a")
    assert isinstance(lease, Lease)
    key = namespace + ":active:total"
    first = connection.zscore(key, lease.lease)

    lease.renew()  # just admitted: nothing to do yet
    assert connection.zscore(key, lease.lease) == first

    lease.renewed -= lease.ttl  # a long transfer, last renewed a while ago
    time.sleep(0.01)
    lease.renew()
    assert connection.zscore(key, lease.lease) > first
    assert connection.ttl(key) >= lease.ttl - 1

    lease()  # released or expired: renewing must not take the slot again
    lease.renewed -= lease.ttl
    lease.renew()
    assert connection.zcard(key) == 0


@pytest.mark.parametrize('seconds_per_piece,allowed', [(1, True), (2, False), (29, False)])
def test_four_gibibytes_at_minimum_rate_or_below(monkeypatch, seconds_per_piece, allowed):
    from apps.api.routers import hls_proxy
    clock = [0.0]
    monkeypatch.setattr(hls_proxy, 'monotonic', lambda: clock[0])
    response, body = _response([], idle=30)
    piece = b'x' * PIECE
    async def pieces():
        for _ in range(4 * 1024**3 // PIECE):
            yield piece
    response.body_iterator = pieces()
    received = [0]
    async def send(message):
        if message.get('body'):
            received[0] += len(message['body'])
            clock[0] += seconds_per_piece
    if allowed:
        _run(response, send)
        assert received[0] == 4 * 1024**3
        assert clock[0] > 18 * 3600
    else:
        with pytest.raises(CUT):
            _run(response, send)
        assert clock[0] <= settings.file_stream_rate_window_seconds + 29
    body.close.assert_called()
    response.release.assert_called()


def test_an_early_burst_cannot_pay_for_later_slow_windows(monkeypatch):
    from apps.api.routers import hls_proxy
    clock = [0.0]
    monkeypatch.setattr(hls_proxy, 'monotonic', lambda: clock[0])
    response, body = _response([], idle=30)
    async def pieces():
        while True:
            yield b'x' * PIECE
    response.body_iterator = pieces()
    async def send(message):
        if message.get('body'):
            clock[0] += 0.5 if clock[0] < 300 else 2
    with pytest.raises(CUT):
        _run(response, send)
    assert clock[0] <= 602
    body.close.assert_called()
    response.release.assert_called()
