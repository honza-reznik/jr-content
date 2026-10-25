"""Access logs retain request context but never signing credentials."""

import logging

from apps.api.main import UvicornAccessLogRedactionFilter, _redact_access_url


def test_access_url_redacts_tokens_tickets_and_signature_values():
    target = (
        "/stream/hls/master.m3u8?token=hls-secret&keep=visible"
        "&X-Amz-Signature=sig-secret&ticket=sse-secret"
    )
    result = _redact_access_url(target)

    assert "hls-secret" not in result
    assert "sig-secret" not in result
    assert "sse-secret" not in result
    assert "keep=visible" in result


def test_uvicorn_access_filter_rewrites_request_target_argument():
    record = logging.LogRecord(
        "uvicorn.access", logging.INFO, "", 0, '%s - "%s %s HTTP/%s" %d',
        ("127.0.0.1", "GET", "/events/p?token=secret", "1.1", 200), None,
    )

    assert UvicornAccessLogRedactionFilter().filter(record)
    assert "secret" not in record.getMessage()
    assert "/events/p" in record.getMessage()
