"""P1-B16: bounded conversions and terminal writes despite broken sessions."""
import json
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import psycopg2
import pytest
from celery.exceptions import Reject, Retry
from sqlalchemy.exc import InterfaceError, OperationalError

from apps.api.models.asset import AssetType, ProcessingStatus
from apps.api.tasks import transcode_tasks as tasks


def disconnected():
    return OperationalError("SELECT 1", {}, Exception("server closed the connection unexpectedly"),
                            connection_invalidated=True)


@pytest.fixture
def conversion():
    version = SimpleNamespace(id=uuid.uuid4(), processing_status=ProcessingStatus.processing)
    terminal_version = SimpleNamespace(id=version.id, processing_status=ProcessingStatus.processing)
    asset = SimpleNamespace(id=uuid.uuid4(), project_id=uuid.uuid4(), asset_type=AssetType.video)
    media = SimpleNamespace(version_id=version.id)
    db, fresh, store = MagicMock(), MagicMock(), MagicMock()
    db.query.return_value.filter.return_value.first.side_effect = [version, asset, media]
    fresh.query.return_value.filter.return_value.first.side_effect = [terminal_version, asset]
    with patch.object(tasks, "SessionLocal", side_effect=[db, fresh]) as sessions, \
         patch.object(tasks, "get_s3_client"), \
         patch.object(tasks, "_process_video") as convert, \
         patch.object(tasks, "_publish_event") as publish, \
         patch.object(tasks, "_failure_store", return_value=store):
        yield SimpleNamespace(version=version, terminal_version=terminal_version,
                              asset=asset, db=db, fresh=fresh, store=store,
                              sessions=sessions, convert=convert, publish=publish)


def run(c, retries):
    task = tasks.process_asset
    task.push_request(retries=retries, called_directly=False)
    try:
        return task.run(str(c.asset.id), str(c.version.id))
    finally:
        task.pop_request()


@pytest.mark.parametrize("phase", ["version", "asset", "media", "processing", "convert", "ready"])
@pytest.mark.parametrize("retries", [0, 1, 2, 3, 7])
def test_database_failures_obey_the_fixed_ceiling(conversion, phase, retries):
    c = conversion
    c.db.query.return_value.filter.return_value.first.side_effect = [
        disconnected() if phase == "version" else c.version,
        disconnected() if phase == "asset" else c.asset,
        disconnected() if phase == "media" else MagicMock(),
    ]
    c.db.commit.side_effect = ([disconnected()] if phase == "processing" else
                               [None, disconnected()] if phase == "ready" else None)
    c.convert.side_effect = disconnected() if phase == "convert" else None
    with patch.object(tasks.process_asset, "retry", side_effect=Retry()) as retry:
        with pytest.raises(Retry if retries < 3 else OperationalError):
            run(c, retries)
        assert tasks.process_asset.max_retries == 3
        if retries < 3:
            retry.assert_called_once()
            assert "max_retries" not in retry.call_args.kwargs
            c.fresh.commit.assert_not_called()
            c.publish.assert_not_called()
        else:
            retry.assert_not_called()
            assert c.terminal_version.processing_status == ProcessingStatus.failed
            c.fresh.commit.assert_called_once()
            assert "database connection failures" in c.publish.call_args.args[2]["error"]
        c.db.rollback.assert_called_once()
        c.db.close.assert_called_once()


@pytest.mark.parametrize("retries", [0, 3])
@pytest.mark.parametrize("broker_down", [False, True])
def test_rollback_disconnect_does_not_bypass_recovery(conversion, retries, broker_down):
    c = conversion
    original = RuntimeError("bad video")
    c.convert.side_effect = original
    c.db.rollback.side_effect = disconnected()
    with patch.object(tasks.process_asset, "retry",
                      side_effect=Reject("broker down", requeue=False) if broker_down else Retry()) as retry:
        with pytest.raises((Retry, Reject, RuntimeError)) as raised:
            run(c, retries)
        assert not isinstance(raised.value, OperationalError), "rollback must not hide the conversion error"
        c.db.close.assert_called_once()
        c.db.commit.assert_called_once()  # processing, never failed on the broken session
        if retries == 0 and not broker_down:
            assert isinstance(raised.value, Retry)
            retry.assert_called_once_with(exc=original)
            c.sessions.assert_called_once()
        else:
            assert c.sessions.call_count == 2
            c.fresh.commit.assert_called_once()
            assert c.terminal_version.processing_status == ProcessingStatus.failed
            assert c.publish.call_args.args[2]["error"] == "bad video"


@pytest.mark.parametrize("phase", ["read", "commit"])
def test_final_status_outage_defers_only_the_status_write(conversion, phase):
    c = conversion
    c.convert.side_effect = disconnected()
    c.db.rollback.side_effect = disconnected()
    if phase == "read":
        c.fresh.query.side_effect = disconnected()
    else:
        c.fresh.commit.side_effect = disconnected()
    c.fresh.rollback.side_effect = disconnected()
    with patch.object(tasks.process_asset, "retry") as retry:
        with pytest.raises(OperationalError):
            run(c, 3)
        retry.assert_not_called()
    c.store.hset.assert_called_once()
    key, version_id, payload = c.store.hset.call_args.args
    assert key == tasks._PENDING_FAILURES and version_id == str(c.version.id)
    assert json.loads(payload) == {"asset_id": str(c.asset.id), "error": tasks._DATABASE_FAILURE}
    c.fresh.close.assert_called_once()
    c.publish.assert_not_called()


def test_pending_status_survives_another_outage_and_finishes_after_recovery():
    version = SimpleNamespace(processing_status=ProcessingStatus.processing)
    asset = SimpleNamespace(project_id=uuid.uuid4())
    asset_id, version_id = str(uuid.uuid4()), str(uuid.uuid4())
    store, broken, fresh = MagicMock(), MagicMock(), MagicMock()
    payload = json.dumps({"asset_id": asset_id, "error": tasks._DATABASE_FAILURE})
    store.hscan_iter.side_effect = lambda key: iter([(version_id, payload)])
    broken.query.side_effect = disconnected()
    broken.rollback.side_effect = disconnected()
    fresh.query.return_value.filter.return_value.first.side_effect = [version, asset]
    with patch.object(tasks, "_failure_store", return_value=store), \
         patch.object(tasks, "SessionLocal", side_effect=[broken, fresh]), \
         patch.object(tasks, "_process_video") as convert, \
         patch.object(tasks, "_publish_event") as publish:
        with pytest.raises(OperationalError):
            tasks.finish_failed_transcodes()
        store.hdel.assert_not_called()
        assert tasks.finish_failed_transcodes() == 1
        store.hdel.assert_called_once_with(tasks._PENDING_FAILURES, version_id)
        assert version.processing_status == ProcessingStatus.failed
        fresh.commit.assert_called_once()
        convert.assert_not_called()
        assert publish.call_args.args[2]["error"] == tasks._DATABASE_FAILURE


def test_terminal_reconciliation_precedes_stuck_conversion_dispatch():
    from apps.api.tasks import cleanup_tasks

    db = MagicMock()
    db.query.return_value.filter.return_value.all.return_value = []
    order = MagicMock()
    with patch.object(cleanup_tasks, "SessionLocal", return_value=db), \
         patch.object(cleanup_tasks.settings, "stuck_processing_timeout_hours", 6), \
         patch.object(tasks, "finish_failed_transcodes") as finish:
        order.attach_mock(finish, "finish")
        order.attach_mock(db.query, "query")
        assert cleanup_tasks.requeue_stuck_processing() == 0
        assert order.mock_calls[0][0] == "finish"


def test_real_celery_retry_ladder_stops_after_four_attempts():
    """Use Celery's retry implementation, not a mocked retry predicate."""
    version = SimpleNamespace(id=uuid.uuid4(), processing_status=ProcessingStatus.processing)
    terminal = SimpleNamespace(id=version.id, processing_status=ProcessingStatus.processing)
    asset = SimpleNamespace(id=uuid.uuid4(), project_id=uuid.uuid4(), asset_type=AssetType.video)
    media = SimpleNamespace(version_id=version.id)
    sessions = []
    for _ in range(4):
        db = MagicMock()
        db.query.return_value.filter.return_value.first.side_effect = [version, asset, media]
        sessions.append(db)
    fresh = MagicMock()
    fresh.query.return_value.filter.return_value.first.side_effect = [terminal, asset]
    with patch.object(tasks, "SessionLocal", side_effect=[*sessions, fresh]), \
         patch.object(tasks, "get_s3_client"), \
         patch.object(tasks, "_process_video", side_effect=disconnected()) as convert, \
         patch.object(tasks.process_asset, "retry", wraps=tasks.process_asset.retry) as retry, \
         patch.object(tasks, "_publish_event") as publish:
        result = tasks.process_asset.apply(args=(str(asset.id), str(version.id)), throw=False)
        assert result.failed()
        assert convert.call_count == 4
        assert retry.call_count == 3
        assert all("max_retries" not in call.kwargs for call in retry.call_args_list)
        assert tasks.process_asset.max_retries == 3
        assert terminal.processing_status == ProcessingStatus.failed
        fresh.commit.assert_called_once()
        assert publish.call_args.args[1] == "transcode_failed"


@pytest.mark.parametrize("error,unavailable", [
    (disconnected(), True),
    (OperationalError("connect", {}, psycopg2.OperationalError("connection refused")), True),
    (InterfaceError("SELECT 1", {}, Exception("connection closed")), True),
    (OperationalError("SELECT 1", {}, SimpleNamespace(pgcode="08006")), True),
    (OperationalError("SELECT 1", {}, SimpleNamespace(pgcode="57P01")), True),
    (OperationalError("SELECT 1", {}, SimpleNamespace(pgcode="40P01")), False),
    (OperationalError("SELECT 1", {}, SimpleNamespace(pgcode="42501")), False),
    (RuntimeError("bad video"), False),
])
def test_connection_error_classification(error, unavailable):
    assert tasks._database_unavailable(error) is unavailable
