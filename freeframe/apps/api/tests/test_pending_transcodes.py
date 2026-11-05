"""P1-B17: committed uploads survive failed dispatch and duplicate delivery."""
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from apps.api.models.asset import ProcessingStatus
from apps.api.tasks import cleanup_tasks, transcode_dispatch, transcode_tasks


@pytest.fixture
def pending():
    marker = SimpleNamespace(version_id=uuid.uuid4(), attempts=0)
    version = SimpleNamespace(id=marker.version_id, asset_id=uuid.uuid4(),
                              deleted_at=None, processing_status=ProcessingStatus.processing)
    db = MagicMock()
    db.__enter__.return_value = db
    db.query.return_value.order_by.return_value.with_for_update.return_value.limit.return_value.all.return_value = [marker]
    db.query.return_value.filter.return_value.first.return_value = version
    with patch.object(transcode_dispatch, "SessionLocal", return_value=db), \
         patch.object(transcode_tasks.process_asset, "apply_async") as send:
        yield SimpleNamespace(marker=marker, version=version, db=db, send=send)


def test_dispatch_survives_broker_outage_and_succeeds_later(pending):
    c = pending
    c.send.side_effect = [ConnectionError("broker unavailable"), None]
    assert transcode_dispatch.dispatch_pending() == 1
    assert c.marker.attempts == 1
    assert c.version.processing_status == ProcessingStatus.processing
    c.db.delete.assert_not_called()
    c.db.commit.assert_called_once()
    assert transcode_dispatch.dispatch_pending() == 1
    c.db.delete.assert_called_once_with(c.marker)
    c.send.assert_called_with(args=[str(c.version.asset_id), str(c.version.id)], retry=False)


def test_failed_dispatch_is_bounded_and_reports_terminal_state(pending):
    c = pending
    c.send.side_effect = ConnectionError("broker unavailable")
    for _ in range(3):
        transcode_dispatch.dispatch_pending()
    assert c.marker.attempts == 3
    assert c.version.processing_status == ProcessingStatus.failed
    c.db.delete.assert_called_once_with(c.marker)


@pytest.mark.parametrize("state", ["missing", "deleted", "ready", "failed"])
def test_obsolete_requests_are_removed_without_processing(pending, state):
    c = pending
    if state == "missing":
        c.db.query.return_value.filter.return_value.first.return_value = None
    elif state == "deleted":
        c.version.deleted_at = object()
    else:
        c.version.processing_status = ProcessingStatus(state)
    transcode_dispatch.dispatch_pending()
    c.send.assert_not_called()
    c.db.delete.assert_called_once_with(c.marker)


@pytest.mark.parametrize("state", [ProcessingStatus.ready, ProcessingStatus.failed, "deleted", "locked"])
def test_worker_skips_duplicate_or_discarded_work(state):
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = SimpleNamespace(
        processing_status=state, deleted_at=object() if state == "deleted" else None)
    lock = db.get_bind.return_value.connect.return_value
    lock.execute.return_value.scalar.return_value = state != "locked"
    with patch.object(transcode_tasks, "SessionLocal", return_value=db), \
         patch.object(transcode_tasks, "_process_video") as convert:
        transcode_tasks.process_asset.run(str(uuid.uuid4()), str(uuid.uuid4()))
    convert.assert_not_called()
    db.commit.assert_not_called()
    lock.close.assert_called_once()
    if state == "locked":
        db.query.assert_not_called()


def test_recovery_budget_stops_infinite_reprocessing():
    version = SimpleNamespace(id=uuid.uuid4(), processing_recovery_attempts=3,
                              processing_status=ProcessingStatus.processing)
    db = MagicMock()
    db.query.return_value.filter.return_value.all.return_value = [version]
    db.query.return_value.filter.return_value.first.return_value = None
    with patch.object(cleanup_tasks, "SessionLocal", return_value=db), \
         patch.object(cleanup_tasks.settings, "stuck_processing_timeout_hours", 6), \
         patch.object(transcode_tasks, "finish_failed_transcodes"), \
         patch.object(transcode_dispatch, "dispatch_pending") as dispatch:
        assert cleanup_tasks.requeue_stuck_processing() == 0
    assert version.processing_status == ProcessingStatus.failed
    assert version.processing_recovery_attempts == 3
    db.execute.assert_not_called()
    dispatch.assert_not_called()
