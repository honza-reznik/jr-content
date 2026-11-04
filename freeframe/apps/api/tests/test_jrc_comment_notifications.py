"""Only managed public comments send a signed, text-free local notification event."""
import hashlib
import hmac
import json
import uuid
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import httpx

from apps.api.services.jrc_comment_notifications import notify_jrc_comment

SECRET = "local-testing-shared-key-1234567890"


def objects():
    return (
        SimpleNamespace(id=uuid.uuid4(), asset_id=uuid.uuid4(), visibility="public", body="SECRET feedback"),
        SimpleNamespace(id=uuid.uuid4(), jrc_managed=True),
    )


def test_managed_comment_sends_signed_metadata_without_text(monkeypatch):
    monkeypatch.setenv("JRC_FF_SSO_SECRET", SECRET)
    comment, user = objects()
    with patch("apps.api.services.jrc_comment_notifications.httpx.post") as post:
        notify_jrc_comment(comment, user)
    url = post.call_args.args[0]
    kwargs = post.call_args.kwargs
    payload = kwargs["data"]
    assert url == "http://jrc-bb-web:8000/video/comment-events/"
    assert json.loads(payload) == {
        "comment_id": str(comment.id), "asset_id": str(comment.asset_id),
        "author_id": str(user.id), "timestamp": json.loads(payload)["timestamp"],
    }
    expected = hmac.new(SECRET.encode(), b"jrc-video-comment.v1." + payload, hashlib.sha256).hexdigest()
    assert kwargs["headers"]["X-JRC-Signature"] == expected
    assert b"SECRET feedback" not in payload
    assert kwargs["follow_redirects"] is False and kwargs["timeout"] == 3
    assert kwargs["trust_env"] is False


@pytest.mark.parametrize("case", ["unmanaged", "internal", "no-secret"])
def test_unrelated_or_internal_comments_do_not_send_events(monkeypatch, case):
    monkeypatch.setenv("JRC_FF_SSO_SECRET", "" if case == "no-secret" else SECRET)
    comment, user = objects()
    if case == "unmanaged":
        user.jrc_managed = False
    if case == "internal":
        comment.visibility = "internal"
    with patch("apps.api.services.jrc_comment_notifications.httpx.post") as post:
        notify_jrc_comment(comment, user)
    post.assert_not_called()


def test_failed_notification_does_not_undo_comment(monkeypatch):
    monkeypatch.setenv("JRC_FF_SSO_SECRET", SECRET)
    comment, user = objects()
    with patch("apps.api.services.jrc_comment_notifications.httpx.post", side_effect=httpx.ReadTimeout("timeout")):
        notify_jrc_comment(comment, user)
