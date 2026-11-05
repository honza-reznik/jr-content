"""P1-B16: disconnected transactions must not strand queued conversions."""
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from celery.exceptions import Retry
from sqlalchemy.exc import OperationalError

from apps.api.models.asset import AssetType, ProcessingStatus
from apps.api.tasks import transcode_tasks


def disconnected():
    return OperationalError("SELECT 1", {}, Exception("server closed the connection unexpectedly"),
                            connection_invalidated=True)


@pytest.mark.parametrize("phase", ["version", "asset", "media", "processing", "convert", "ready"])
@pytest.mark.parametrize("retries", [0, 3, 7])
def test_every_database_phase_retries_even_after_normal_budget(phase, retries):
    version = SimpleNamespace(id=uuid.uuid4(), processing_status=ProcessingStatus.processing)
    asset = SimpleNamespace(id=uuid.uuid4(), project_id=uuid.uuid4(), asset_type=AssetType.video)
    media = SimpleNamespace(version_id=version.id)
    db = MagicMock()
    db.query.return_value.filter.return_value.first.side_effect = [
        disconnected() if phase == "version" else version,
        disconnected() if phase == "asset" else asset,
        disconnected() if phase == "media" else media,
    ]
    db.commit.side_effect = ([disconnected()] if phase == "processing" else
                             [None, disconnected()] if phase == "ready" else None)
    task = transcode_tasks.process_asset
    task.push_request(retries=retries, called_directly=False)
    try:
        with patch.object(transcode_tasks, "SessionLocal", return_value=db), \
             patch.object(transcode_tasks, "get_s3_client"), \
             patch.object(transcode_tasks, "_process_video",
                          side_effect=disconnected() if phase == "convert" else None), \
             patch.object(transcode_tasks, "_publish_event") as publish, \
             patch.object(task, "retry", side_effect=Retry()) as retry:
            with pytest.raises(Retry):
                task.run(str(asset.id), str(version.id))
            assert retry.call_args.kwargs["max_retries"] > retries
            assert isinstance(retry.call_args.kwargs["exc"], OperationalError)
            db.rollback.assert_called()
            db.close.assert_called_once()
            publish.assert_not_called()
    finally:
        task.pop_request()


def test_failure_status_uses_a_rolled_back_transaction():
    version = SimpleNamespace(id=uuid.uuid4(), processing_status=ProcessingStatus.processing)
    asset = SimpleNamespace(id=uuid.uuid4(), project_id=uuid.uuid4(), asset_type=AssetType.video)
    db = MagicMock()
    db.query.return_value.filter.return_value.first.side_effect = [version, asset, MagicMock()]
    usable = True

    def convert(*args, **kwargs):
        nonlocal usable
        usable = False
        raise RuntimeError("conversion failed with an aborted transaction")

    def rollback():
        nonlocal usable
        usable = True

    def commit():
        assert usable, "failed status must not commit an aborted transaction"

    db.rollback.side_effect, db.commit.side_effect = rollback, commit
    task = transcode_tasks.process_asset
    task.push_request(retries=3, called_directly=False)
    try:
        with patch.object(transcode_tasks, "SessionLocal", return_value=db), \
             patch.object(transcode_tasks, "get_s3_client"), \
             patch.object(transcode_tasks, "_process_video", side_effect=convert), \
             patch.object(transcode_tasks, "_publish_event") as publish:
            with pytest.raises(RuntimeError):
                task.run(str(asset.id), str(version.id))
            assert version.processing_status == ProcessingStatus.failed
            assert publish.call_args.args[1] == "transcode_failed"
    finally:
        task.pop_request()


def test_database_outage_while_recording_terminal_failure_is_retried():
    version = SimpleNamespace(id=uuid.uuid4(), processing_status=ProcessingStatus.processing)
    asset = SimpleNamespace(id=uuid.uuid4(), project_id=uuid.uuid4(), asset_type=AssetType.video)
    db = MagicMock()
    db.query.return_value.filter.return_value.first.side_effect = [version, asset, MagicMock()]
    db.commit.side_effect = [None, disconnected()]
    task = transcode_tasks.process_asset
    task.push_request(retries=3, called_directly=False)
    try:
        with patch.object(transcode_tasks, "SessionLocal", return_value=db), \
             patch.object(transcode_tasks, "get_s3_client"), \
             patch.object(transcode_tasks, "_process_video", side_effect=RuntimeError("bad video")), \
             patch.object(transcode_tasks, "_publish_event") as publish, \
             patch.object(task, "retry", side_effect=Retry()):
            with pytest.raises(Retry):
                task.run(str(asset.id), str(version.id))
            publish.assert_not_called()
    finally:
        task.pop_request()
