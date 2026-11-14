"""P2-B18: bounded trickles, independent rate windows, and a 4 GiB slow transfer without wall-clock waits."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from apps.jrc_storage import views


@pytest.mark.parametrize("seconds_per_piece,allowed", [(1, True), (2, False), (29, False)])
def test_four_gibibytes_at_minimum_speed_or_below(monkeypatch, seconds_per_piece, allowed):
    clock = [0.0]
    monkeypatch.setattr(views, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    count = [0]
    piece = b"x" * views.CHUNK_BYTES

    def read(size):
        assert size == views.CHUNK_BYTES
        count[0] += 1
        return piece if count[0] <= 4 * 1024**3 // size else b""

    close, release = Mock(), Mock()
    response = views._stream(read, close, release)
    received = 0

    def consume():
        nonlocal received
        for chunk in response.streaming_content:
            received += len(chunk)
            clock[0] += seconds_per_piece

    if allowed:
        consume()
        assert received == 4 * 1024**3
        assert clock[0] > 18 * 3600
    else:
        with pytest.raises(RuntimeError, match="too slow"):
            consume()
        assert clock[0] <= views.RATE_WINDOW_SECONDS + 29
    close.assert_called()
    release.assert_called()


def test_fast_first_window_cannot_buy_an_unlimited_trickle(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(views, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    close, release = Mock(), Mock()
    pieces = iter(views._stream(lambda size: b"x" * size, close, release).streaming_content)
    # The first 300s window transfers at twice the minimum, then drops below it.
    for _ in range(600):
        next(pieces)
        clock[0] += 0.5
    with pytest.raises(RuntimeError, match="too slow"):
        while True:
            next(pieces)
            clock[0] += 2
    assert clock[0] <= 602
    close.assert_called()
    release.assert_called()
