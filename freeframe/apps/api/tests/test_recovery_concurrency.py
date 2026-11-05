"""P1-B17 review: real transactions protect completed and running conversions."""
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import BackgroundTasks
from sqlalchemy import text

from apps.api.database import SessionLocal, engine
from apps.api.models.asset import Asset, AssetType, AssetVersion, MediaFile, ProcessingStatus
from apps.api.models.pending_transcode import PendingTranscode
from apps.api.models.project import Project, ProjectType
from apps.api.models.user import User
from apps.api.routers import upload
from apps.api.schemas.upload import AbortUploadRequest
from apps.api.tasks import cleanup_tasks, transcode_tasks


@pytest.fixture
def saved_version(monkeypatch):
    """Committed rows visible to independent connections, removed even on failure."""
    user_id, project_id, asset_id, version_id = [uuid.uuid4() for _ in range(4)]
    try:
        with SessionLocal() as db:
            db.add(User(id=user_id, email=f"recovery-{user_id}@t.local", name="test"))
            db.flush()
            db.add(Project(id=project_id, name="recovery", created_by=user_id, project_type=ProjectType.personal))
            db.flush()
            db.add(Asset(id=asset_id, project_id=project_id, name="recovery", created_by=user_id,
                         asset_type=AssetType.video))
            db.flush()
            db.add(AssetVersion(id=version_id, asset_id=asset_id, version_number=1, created_by=user_id,
                                processing_status=ProcessingStatus.processing, processing_recovery_attempts=3,
                                upload_id="test-upload", last_activity_at=datetime.now(timezone.utc) - timedelta(hours=7)))
            db.commit()
        monkeypatch.setattr(cleanup_tasks.settings, "stuck_processing_timeout_hours", 6)
        monkeypatch.setattr(transcode_tasks, "finish_failed_transcodes", lambda: 0)
        monkeypatch.setattr(transcode_tasks.process_asset, "apply_async", lambda *a, **k: pytest.fail("no redispatch expected"))
        def recovery_session():
            session = SessionLocal()
            query = session.query

            def own_version_only(*entities):
                result = query(*entities)
                return result.filter(AssetVersion.id == version_id) if entities == (AssetVersion,) else result

            monkeypatch.setattr(session, "query", own_version_only)
            return session

        # Never sweep unrelated rows, even if the suite is run in a populated DB.
        monkeypatch.setattr(cleanup_tasks, "SessionLocal", recovery_session)
        yield SimpleNamespace(user_id=user_id, asset_id=asset_id, version_id=version_id, session=recovery_session,
                              lock_key=version_id.int % (2 ** 63))
    finally:
        with SessionLocal() as db:
            db.query(PendingTranscode).filter_by(version_id=version_id).delete()
            db.query(MediaFile).filter_by(version_id=version_id).delete()
            db.query(AssetVersion).filter_by(id=version_id).delete()
            db.query(Asset).filter_by(id=asset_id).delete()
            db.query(Project).filter_by(id=project_id).delete()
            db.query(User).filter_by(id=user_id).delete()
            db.commit()


def test_recovery_leaves_a_running_worker_untouched(saved_version):
    c = saved_version
    with engine.connect() as worker:
        worker.execute(text("SELECT pg_advisory_lock(:key)"), {"key": c.lock_key})
        worker.commit()
        try:
            assert cleanup_tasks.requeue_stuck_processing() == 0
            with SessionLocal() as db:
                assert db.get(AssetVersion, c.version_id).processing_status == ProcessingStatus.processing
        finally:
            worker.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": c.lock_key})
            worker.commit()


def test_recovery_refreshes_completion_committed_after_its_scan(saved_version, monkeypatch):
    c = saved_version
    with c.session() as recovery:
        execute = recovery.execute

        def finish_before_lock(statement, *args, **kwargs):
            if "pg_try_advisory_xact_lock" in str(statement):
                # Initial scan saw processing and no output; the worker has now
                # committed ready. The ORM identity map still holds the old row.
                with SessionLocal() as worker:
                    worker.get(AssetVersion, c.version_id).processing_status = ProcessingStatus.ready
                    worker.commit()
            return execute(statement, *args, **kwargs)

        monkeypatch.setattr(recovery, "execute", finish_before_lock)
        monkeypatch.setattr(cleanup_tasks, "SessionLocal", lambda: recovery)
        assert cleanup_tasks.requeue_stuck_processing() == 0
    with SessionLocal() as db:
        assert db.get(AssetVersion, c.version_id).processing_status == ProcessingStatus.ready
        assert db.get(PendingTranscode, c.version_id) is None


def test_recovery_holds_worker_lock_until_terminal_commit(saved_version, monkeypatch):
    c = saved_version
    with c.session() as recovery, engine.connect() as worker:
        commit = recovery.commit

        def check_lock_and_commit():
            acquired = worker.execute(text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": c.lock_key}).scalar()
            worker.rollback()
            assert acquired is False
            commit()

        monkeypatch.setattr(recovery, "commit", check_lock_and_commit)
        monkeypatch.setattr(cleanup_tasks, "SessionLocal", lambda: recovery)
        assert cleanup_tasks.requeue_stuck_processing() == 0
        assert worker.execute(text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": c.lock_key}).scalar() is True
        worker.rollback()
    with SessionLocal() as db:
        assert db.get(AssetVersion, c.version_id).processing_status == ProcessingStatus.failed


def test_abort_recovery_commits_status_and_dispatch_marker_together(saved_version, monkeypatch):
    c = saved_version
    with SessionLocal() as db:
        version = db.get(AssetVersion, c.version_id)
        version.processing_status = ProcessingStatus.uploading
        db.add(MediaFile(version_id=c.version_id, s3_key_raw="raw/recovery-test", file_size_bytes=123,
                         file_type="video", original_filename="test.mp4", mime_type="video/mp4"))
        db.commit()
        monkeypatch.setattr(upload, "abort_multipart_upload", lambda *a: None)
        monkeypatch.setattr(upload, "head_object_size", lambda *a: 123)
        background = BackgroundTasks()
        upload.abort_upload(AbortUploadRequest(version_id=c.version_id, upload_id="test-upload",
                                               s3_key="raw/recovery-test"),
                            background, db=db, current_user=db.get(User, c.user_id))
    with SessionLocal() as fresh:
        assert fresh.get(AssetVersion, c.version_id).processing_status == ProcessingStatus.processing
        assert fresh.get(PendingTranscode, c.version_id) is not None
    # Run the actual dispatcher, not a replacement for _trigger_processing.
    sent = []
    monkeypatch.setattr(transcode_tasks.process_asset, "apply_async", lambda **kwargs: sent.append(kwargs))
    for task in background.tasks:
        task.func(*task.args, **task.kwargs)
    assert sent == [{"args": [str(c.asset_id), str(c.version_id)], "retry": False}]
    with SessionLocal() as fresh:
        assert fresh.get(PendingTranscode, c.version_id) is None


def test_pending_transcode_is_removed_with_its_version(saved_version):
    """The cleanup FK guard needs proof that PostgreSQL handles the outbox row."""
    c = saved_version
    with SessionLocal() as db:
        db.add(PendingTranscode(version_id=c.version_id))
        db.commit()
        db.query(AssetVersion).filter_by(id=c.version_id).delete(synchronize_session=False)
        db.commit()
    with SessionLocal() as fresh:
        assert fresh.get(PendingTranscode, c.version_id) is None
