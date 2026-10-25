"""Cookie-based HLS requests and a streamed private S3 object for tests."""
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import jwt

from apps.api.config import settings
from apps.api.routers.hls_proxy import hls_session_id


def hls_url(client, token, path):
    payload = jwt.decode(token, options={"verify_signature": False})
    root = f'/stream/hls/{hls_session_id(payload["pfx"])}/'
    client.cookies.set("ff_hls", token, path=root)
    return root + path


@contextmanager
def segment_storage():
    body = MagicMock()
    body.iter_chunks.return_value = iter([b"transport-stream"])
    storage = MagicMock()
    storage.get_object.return_value = {"Body": body, "ContentLength": len(b"transport-stream")}
    with patch("apps.api.routers.hls_proxy._stream_s3_client", return_value=storage):
        yield storage, body
