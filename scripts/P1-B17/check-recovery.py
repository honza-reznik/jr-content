"""Živý PostgreSQL: trvalá fronta, souběh, rozpočet obnovy a zámek převodu.

Pouze jrc-p1b17, po upload scénáři, se zastaveným beat/maintenance.
Odeslání do fronty je nahrazené; testovací verze nemají média.
"""
import os
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from sqlalchemy import text

from apps.api.database import SessionLocal, engine
from apps.api.models.asset import Asset, AssetVersion, ProcessingStatus
from apps.api.models.pending_transcode import PendingTranscode
from apps.api.tasks import cleanup_tasks, transcode_dispatch, transcode_tasks

assert os.environ["JRC_PROJECT"] == "jrc-p1b17"
assert os.environ["JRC_PUBLIC_ORIGIN"] == "http://127.0.0.1:13593"
asset_id, first, second = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
try:
    with SessionLocal() as db:
        source = db.query(Asset).filter(Asset.deleted_at.is_(None)).first()
        assert source is not None, "Nejprve skutečně nahraj video."
        db.add(Asset(id=asset_id, project_id=source.project_id, name="P1-B17 recovery probe",
                     created_by=source.created_by, asset_type=source.asset_type))
        db.flush()
        for number, version_id in enumerate([first, second], 1):
            db.add(AssetVersion(id=version_id, asset_id=asset_id, version_number=number,
                                created_by=source.created_by, processing_status=ProcessingStatus.processing))
        db.flush()
        db.add_all([PendingTranscode(version_id=first), PendingTranscode(version_id=second)])
        db.commit()

    # Another dispatcher must skip a marker claimed by a concurrent transaction.
    with SessionLocal() as locked, patch.object(transcode_tasks.process_asset, "apply_async") as send:
        locked.query(PendingTranscode).filter_by(version_id=first).with_for_update().one()
        assert transcode_dispatch.dispatch_pending(first) == 0
        send.assert_not_called()
        locked.rollback()

    with patch.object(transcode_tasks.process_asset, "apply_async", side_effect=ConnectionError("test outage")):
        assert transcode_dispatch.dispatch_pending(first) == 1
    # Check a fresh session, not the in-memory model from the failed publisher.
    with SessionLocal() as db:
        assert db.get(PendingTranscode, first).attempts == 1
        assert db.get(AssetVersion, first).processing_status == ProcessingStatus.processing
    with patch.object(transcode_tasks.process_asset, "apply_async") as send:
        assert transcode_dispatch.dispatch_pending(first) == 1
        send.assert_called_once_with(args=[str(asset_id), str(first)], retry=False)
    with SessionLocal() as db:
        assert db.get(PendingTranscode, first) is None
    print("OK PostgreSQL: požadavek přežil výpadek i nové spojení; souběh jej neodeslal dvakrát.")

    with patch.object(transcode_tasks.process_asset, "apply_async", side_effect=ConnectionError("test outage")):
        for _ in range(3):
            transcode_dispatch.dispatch_pending(second)
    with SessionLocal() as db:
        assert db.get(AssetVersion, second).processing_status == ProcessingStatus.failed
        assert db.get(PendingTranscode, second) is None
    print("OK PostgreSQL: tři odmítnutí frontou ukončí verzi chybou.")

    # A duplicate task cannot touch the version while another worker owns its lock.
    key = first.int % (2 ** 63)
    with engine.connect() as lock, patch.object(transcode_tasks, "_process_video") as convert:
        lock.execute(text("SELECT pg_advisory_lock(:key)"), {"key": key})
        lock.commit()
        try:
            transcode_tasks.process_asset.run(str(asset_id), str(first))
            convert.assert_not_called()
        finally:
            lock.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": key})
            lock.commit()
    print("OK PostgreSQL: souběžný převod stejné verze přeskočen.")

    with SessionLocal() as db:
        v = db.get(AssetVersion, first)
        v.last_activity_at = datetime.now(timezone.utc) - timedelta(hours=7)
        v.processing_recovery_attempts = 2
        db.commit()
    with patch.object(transcode_tasks.process_asset, "apply_async") as send:
        assert cleanup_tasks.requeue_stuck_processing() == 1
        send.assert_called_once()
    with SessionLocal() as db:
        v = db.get(AssetVersion, first)
        assert v.processing_recovery_attempts == 3
        v.last_activity_at = datetime.now(timezone.utc) - timedelta(hours=7)
        db.commit()
    with patch.object(transcode_tasks.process_asset, "apply_async") as send:
        assert cleanup_tasks.requeue_stuck_processing() == 0
        send.assert_not_called()
    with SessionLocal() as db:
        assert db.get(AssetVersion, first).processing_status == ProcessingStatus.failed
    print("OK PostgreSQL: kontrola starých verzí obnovuje s trvalým stropem tří pokusů.")
finally:
    with SessionLocal() as db:
        db.query(PendingTranscode).filter(PendingTranscode.version_id.in_([first, second])).delete(synchronize_session=False)
        db.query(AssetVersion).filter(AssetVersion.asset_id == asset_id).delete(synchronize_session=False)
        db.query(Asset).filter(Asset.id == asset_id).delete(synchronize_session=False)
        db.commit()
