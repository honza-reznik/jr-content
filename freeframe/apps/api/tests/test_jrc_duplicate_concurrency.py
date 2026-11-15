"""JR Content P2-B15 (review fix): purge and duplicate of a shared file on two real connections.

Each side runs in its own session and transaction, so the row locks are real. A purge decides
"nobody else names this object" only after locking every row that names it; a duplicate locks the
source's media row. Whoever comes second waits for the first and then sees its result, so a file
is neither deleted under a new copy nor left behind for ever. Storage deletes run after the commit.
"""
import threading
import time
import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy.orm import sessionmaker

import apps.api.tasks.cleanup_tasks as ct
from apps.api.database import engine
from apps.api.models.activity import ActivityLog
from apps.api.models.asset import Asset, AssetType, AssetVersion, FileType, MediaFile, ProcessingStatus
from apps.api.models.project import Project, ProjectMember, ProjectRole, ProjectType
from apps.api.models.user import User
from apps.api.services.jrc_duplicate import duplicate_version

Session = sessionmaker(bind=engine, expire_on_commit=False)
KEYS = ("s3_key_raw", "s3_key_processed", "s3_key_download", "s3_key_thumbnail")


@pytest.fixture
def shared(monkeypatch):
    """Two committed branches (A, B) naming the same four objects, removed again afterwards."""
    deleted = []
    monkeypatch.setattr(ct, "delete_object", lambda key: deleted.append(key))
    monkeypatch.setattr(ct, "delete_prefix", lambda key: deleted.append(key))
    db = Session()
    user = User(email=f"conc-{uuid.uuid4()}@t.local", name="conc", jrc_managed=True)
    db.add(user)
    db.flush()
    project = Project(name="Conc", project_type=ProjectType.personal, created_by=user.id)
    db.add(project)
    db.flush()
    db.add(ProjectMember(project_id=project.id, user_id=user.id, role=ProjectRole.editor))
    base = f"conc/{uuid.uuid4()}"
    names = dict(
        s3_key_raw=f"{base}/original.mov", s3_key_processed=f"{base}/hls",
        s3_key_download=f"{base}/hls/download.mp4", s3_key_thumbnail=f"{base}/hls/thumb.jpg",
    )
    versions, assets = [], []
    for label in ("A", "B"):
        asset = Asset(project_id=project.id, name=f"Branch {label}", asset_type=AssetType.video, created_by=user.id)
        db.add(asset)
        db.flush()
        version = AssetVersion(
            asset_id=asset.id, version_number=1, processing_status=ProcessingStatus.ready, created_by=user.id,
        )
        db.add(version)
        db.flush()
        db.add(MediaFile(
            version_id=version.id, file_type=FileType.video, original_filename="reel.mov",
            mime_type="video/quicktime", file_size_bytes=1000, **names,
        ))
        versions.append(version.id)
        assets.append(asset.id)
    db.commit()
    ids = dict(user=user.id, project=project.id, assets=assets, versions=versions, keys=list(names.values()))
    ids["deleted"] = deleted
    db.close()
    yield ids
    cleanup = Session()
    try:
        asset_ids = [r.id for r in cleanup.query(Asset.id).filter(Asset.project_id == ids["project"])]
        version_ids = [r.id for r in cleanup.query(AssetVersion.id).filter(AssetVersion.asset_id.in_(asset_ids))]
        cleanup.query(MediaFile).filter(MediaFile.version_id.in_(version_ids)).delete(synchronize_session=False)
        cleanup.query(ActivityLog).filter(ActivityLog.project_id == ids["project"]).delete(synchronize_session=False)
        cleanup.query(AssetVersion).filter(AssetVersion.id.in_(version_ids)).delete(synchronize_session=False)
        cleanup.query(Asset).filter(Asset.id.in_(asset_ids)).delete(synchronize_session=False)
        cleanup.query(ProjectMember).filter(ProjectMember.project_id == ids["project"]).delete(synchronize_session=False)
        cleanup.query(Project).filter(Project.id == ids["project"]).delete(synchronize_session=False)
        cleanup.query(User).filter(User.id == ids["user"]).delete(synchronize_session=False)
        cleanup.commit()
    finally:
        cleanup.close()


def _in_thread(fn):
    """Run fn in a thread; returns (thread, result) where result gets ('ok', value) or ('error', exc)."""
    result = []

    def run():
        try:
            result.append(("ok", fn()))
        except Exception as exc:  # noqa: BLE001 - handed back to the test
            result.append(("error", exc))

    thread = threading.Thread(target=run)
    thread.start()
    return thread, result


def _still_waiting(thread) -> bool:
    time.sleep(0.7)
    return thread.is_alive()


def test_two_purges_of_the_two_branches_leave_no_orphan_and_delete_once(shared):
    first, second = Session(), Session()
    try:
        ct._purge_version(first, shared["versions"][0], ct.PurgeCounts())  # locks the shared rows, not committed
        started = time.monotonic()

        def purge_other():
            ct._purge_version(second, shared["versions"][1], ct.PurgeCounts())
            second.commit()

        thread, result = _in_thread(purge_other)
        assert _still_waiting(thread), "the second purge must wait for the first"
        assert shared["deleted"] == []
        first.commit()  # branch A is gone; branch B is now the last row
        thread.join(10)
        assert result and result[0][0] == "ok", result
        assert sorted(shared["deleted"]) == sorted(shared["keys"])  # exactly once, after both commits
        assert time.monotonic() - started < 10
    finally:
        first.close()
        second.close()


def test_a_purge_that_rolls_back_deletes_nothing(shared):
    db = Session()
    try:
        ct._purge_version(db, shared["versions"][0], ct.PurgeCounts())
        ct._purge_version(db, shared["versions"][1], ct.PurgeCounts())  # both gone in this transaction
        db.rollback()
        assert shared["deleted"] == []
        assert Session().query(MediaFile).filter(MediaFile.s3_key_raw == shared["keys"][0]).count() == 2
    finally:
        db.close()


def test_duplicate_waits_for_a_purge_of_its_source_and_then_refuses(shared):
    purger, copier = Session(), Session()
    try:
        user = copier.query(User).filter(User.id == shared["user"]).one()
        ct._purge_version(purger, shared["versions"][0], ct.PurgeCounts())  # source branch being purged

        def copy():
            return duplicate_version(copier, user, shared["versions"][0], asset_name="Late copy")

        thread, result = _in_thread(copy)
        assert _still_waiting(thread), "the duplicate must wait for the purge"
        purger.commit()
        thread.join(10)
        assert result and result[0][0] == "error" and isinstance(result[0][1], HTTPException)
        assert result[0][1].status_code in (404, 409)
        copier.rollback()
        # The other branch kept the objects: the purge of A deleted nothing.
        assert shared["deleted"] == []
    finally:
        purger.close()
        copier.close()


def test_purge_waits_for_a_duplicate_and_then_keeps_the_file(shared):
    """The duplicate is first; the purge of the last other row must see the new copy."""
    copier, purger = Session(), Session()
    try:
        user = copier.query(User).filter(User.id == shared["user"]).one()
        # Branch B is already purged, so the source (A) is the only row naming the files.
        ct._purge_version(purger, shared["versions"][1], ct.PurgeCounts())
        purger.commit()
        shared["deleted"].clear()
        result = duplicate_version(copier, user, shared["versions"][0], asset_name="Copy")  # holds the lock

        def purge_source():
            ct._purge_version(purger, shared["versions"][0], ct.PurgeCounts())
            purger.commit()

        thread, outcome = _in_thread(purge_source)
        assert _still_waiting(thread), "the purge must wait for the duplicate"
        copier.commit()
        thread.join(10)
        assert outcome and outcome[0][0] == "ok", outcome
        assert shared["deleted"] == []  # the new copy still names every object
        assert Session().query(MediaFile).filter(MediaFile.version_id == result["version_id"]).count() == 1
    finally:
        copier.close()
        purger.close()
        extra = Session()
        try:  # the copy's asset was created here; remove it so the fixture can drop the project
            made = [r.id for r in extra.query(Asset.id).filter(Asset.project_id == shared["project"], Asset.name == "Copy")]
            vs = [r.id for r in extra.query(AssetVersion.id).filter(AssetVersion.asset_id.in_(made))]
            extra.query(MediaFile).filter(MediaFile.version_id.in_(vs)).delete(synchronize_session=False)
            extra.query(ActivityLog).filter(ActivityLog.asset_id.in_(made)).delete(synchronize_session=False)
            extra.query(AssetVersion).filter(AssetVersion.id.in_(vs)).delete(synchronize_session=False)
            extra.query(Asset).filter(Asset.id.in_(made)).delete(synchronize_session=False)
            extra.commit()
        finally:
            extra.close()
