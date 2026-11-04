"""Durable delivery, transaction boundaries, fresh signatures and receiver deduplication."""

import hashlib
import hmac
import json
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import httpx
import pytest

from apps.api.models.jrc_comment_event import JrcCommentEvent
from apps.api.services.jrc_comment_notifications import (
    queue_jrc_comment,
    deliver_next_jrc_comment,
)

SECRET = "local-testing-shared-key-1234567890"


def objects():
    return (
        SimpleNamespace(
            id=uuid.uuid4(),
            asset_id=uuid.uuid4(),
            visibility="public",
            body="SECRET feedback",
        ),
        SimpleNamespace(id=uuid.uuid4(), jrc_managed=True),
    )


def test_managed_comment_queues_only_metadata_even_without_delivery_key(
    monkeypatch, mock_db
):
    monkeypatch.delenv("JRC_FF_SSO_SECRET", raising=False)
    comment, user = objects()
    queue_jrc_comment(mock_db, comment, user)
    event = mock_db.add.call_args.args[0]
    assert isinstance(event, JrcCommentEvent)
    assert (event.comment_id, event.asset_id, event.author_id) == (
        comment.id,
        comment.asset_id,
        user.id,
    )
    assert not hasattr(event, "body")
    mock_db.commit.assert_not_called()


@pytest.mark.parametrize("case", ["unmanaged", "internal", "guest"])
def test_unrelated_or_internal_comments_do_not_queue_events(mock_db, case):
    comment, user = objects()
    if case == "unmanaged":
        user.jrc_managed = False
    elif case == "internal":
        comment.visibility = "internal"
    else:
        user = None
    queue_jrc_comment(mock_db, comment, user)
    mock_db.add.assert_not_called()


def seed(db):
    from apps.api.models.user import User
    from apps.api.models.project import Project, ProjectType
    from apps.api.models.asset import Asset, AssetType, AssetVersion, ProcessingStatus
    from apps.api.models.comment import Comment

    user = User(
        email=f"comment-{uuid.uuid4()}@example.invalid", name="Client", jrc_managed=True
    )
    db.add(user)
    db.flush()
    project = Project(
        name="Comment delivery", project_type=ProjectType.personal, created_by=user.id
    )
    db.add(project)
    db.flush()
    asset = Asset(
        project_id=project.id,
        name="Video",
        asset_type=AssetType.video,
        created_by=user.id,
    )
    db.add(asset)
    db.flush()
    version = AssetVersion(
        asset_id=asset.id,
        version_number=1,
        processing_status=ProcessingStatus.ready,
        created_by=user.id,
    )
    db.add(version)
    db.flush()
    comment = Comment(
        asset_id=asset.id,
        version_id=version.id,
        author_id=user.id,
        body="PRIVATE TEXT",
        visibility="public",
    )
    db.add(comment)
    db.flush()
    queue_jrc_comment(db, comment, user)
    db.flush()
    return comment


def test_comment_and_pending_event_rollback_together(real_db):
    from apps.api.models.comment import Comment

    with real_db.begin_nested() as savepoint:
        comment = seed(real_db)
        comment_id = comment.id
        assert real_db.get(JrcCommentEvent, comment_id) is not None
        savepoint.rollback()
    assert real_db.get(Comment, comment_id) is None
    assert real_db.get(JrcCommentEvent, comment_id) is None


@pytest.mark.parametrize("failure", ["connection", "timeout", "server"])
def test_outage_recovers_after_session_restart_with_fresh_signature_once(
    real_db, monkeypatch, failure
):
    monkeypatch.setenv("JRC_FF_SSO_SECRET", SECRET)
    comment = seed(real_db)
    event = real_db.get(JrcCommentEvent, comment.id)
    error = {
        "connection": httpx.ConnectError("refused"),
        "timeout": httpx.ReadTimeout("timeout"),
        "server": httpx.HTTPStatusError(
            "500",
            request=httpx.Request("POST", "http://local"),
            response=httpx.Response(500),
        ),
    }[failure]
    with patch(
        "apps.api.services.jrc_comment_notifications.httpx.post", side_effect=error
    ) as post:
        assert deliver_next_jrc_comment(real_db)
        first = post.call_args.kwargs
    real_db.flush()
    real_db.expunge_all()
    event = real_db.get(JrcCommentEvent, comment.id)
    assert event.delivered_at is None and event.attempts == 1
    assert event.next_attempt_at > datetime.now(timezone.utc)
    assert not deliver_next_jrc_comment(real_db), "Backoff must survive session reload"
    event.next_attempt_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    real_db.flush()
    with (
        patch(
            "apps.api.services.jrc_comment_notifications.time.time",
            return_value=json.loads(first["content"])["timestamp"] + 120,
        ),
        patch(
            "apps.api.services.jrc_comment_notifications.httpx.post",
            return_value=httpx.Response(
                204, request=httpx.Request("POST", "http://local")
            ),
        ) as post,
    ):
        assert deliver_next_jrc_comment(real_db)
        real_db.flush()
        assert not deliver_next_jrc_comment(real_db)
        post.assert_called_once()
        second = post.call_args.kwargs
    assert event.delivered_at is not None and event.attempts == 2
    for attempt in (first, second):
        payload = attempt["content"]
        data = json.loads(payload)
        assert set(data) == {"comment_id", "asset_id", "author_id", "timestamp"}
        assert data["comment_id"] == str(comment.id)
        assert b"PRIVATE TEXT" not in payload
        expected = hmac.new(
            SECRET.encode(), b"jrc-video-comment.v1." + payload, hashlib.sha256
        ).hexdigest()
        assert attempt["headers"]["X-JRC-Signature"] == expected
        assert (
            attempt["follow_redirects"] is False
            and attempt["timeout"] == 3
            and attempt["trust_env"] is False
        )
    assert first["headers"]["X-JRC-Signature"] != second["headers"]["X-JRC-Signature"]


def test_missing_key_retains_pending_event(real_db, monkeypatch):
    monkeypatch.delenv("JRC_FF_SSO_SECRET", raising=False)
    comment = seed(real_db)
    with patch("apps.api.services.jrc_comment_notifications.httpx.post") as post:
        assert deliver_next_jrc_comment(real_db)
    post.assert_not_called()
    assert real_db.get(JrcCommentEvent, comment.id).delivered_at is None


def test_purging_comment_removes_its_event(real_db):
    from apps.api.tasks.cleanup_tasks import _purge_comment, PurgeCounts

    comment = seed(real_db)
    comment_id = comment.id
    _purge_comment(real_db, comment_id, PurgeCounts())
    real_db.flush()
    real_db.expunge_all()
    assert real_db.get(JrcCommentEvent, comment_id) is None


def test_sweeper_recovers_pending_rows_without_request_dispatch(monkeypatch):
    from apps.api.tasks import jrc_comment_tasks as tasks
    from apps.api.tasks.celery_app import celery_app

    factory = MagicMock()
    monkeypatch.setattr(tasks, "SessionLocal", factory)
    with patch.object(
        tasks, "deliver_next_jrc_comment", side_effect=[True, True, False]
    ) as delivery:
        tasks.deliver_jrc_comment_events.run()
    assert delivery.call_count == 3
    assert factory.return_value.__enter__.return_value.begin.call_count == 3
    assert tasks.deliver_jrc_comment_events.queue == "maintenance"
    assert (
        celery_app.conf.beat_schedule["jrc-comment-events"]["task"]
        == tasks.deliver_jrc_comment_events.name
    )


@pytest.mark.parametrize("reply", [False, True])
def test_comment_route_commits_outbox_with_comment_before_dispatch(
    real_db, monkeypatch, reply
):
    from sqlalchemy import event as sql_event
    from apps.api.models.user import User
    from apps.api.routers import comments as router
    from apps.api.schemas.comment import CommentCreate

    parent = seed(real_db)
    user = real_db.get(User, parent.author_id)
    for name in (
        "require_asset_access",
        "_require_managed_comment_role",
        "_require_public_comment_input",
        "_create_mentions",
    ):
        monkeypatch.setattr(router, name, lambda *args: None)
    monkeypatch.setattr(router, "hides_internal_comments", lambda *args: False)
    monkeypatch.setattr(router.event_service, "publish_sync", lambda *args: None)
    monkeypatch.setattr(
        router, "_build_comment_response", lambda comment, *args, **kwargs: comment
    )
    committed = []

    def before_commit(db):
        pending = [row for row in db.new if isinstance(row, JrcCommentEvent)]
        assert len(pending) == 1
        committed.append(pending[0].comment_id)

    sql_event.listen(real_db, "before_commit", before_commit)
    try:
        with patch.object(router, "send_task_safe") as dispatch:
            body = CommentCreate(
                version_id=parent.version_id, body="new comment", timecode_start=17
            )
            if reply:
                result = router.reply_to_comment(
                    parent.asset_id, parent.id, body, db=real_db, current_user=user
                )
            else:
                result = router.create_comment(
                    parent.asset_id, body, db=real_db, current_user=user
                )
            dispatch.assert_called_once_with(router.deliver_jrc_comment_events)
        assert committed == [result.id]
        assert real_db.get(JrcCommentEvent, result.id).delivered_at is None
    finally:
        sql_event.remove(real_db, "before_commit", before_commit)
