"""Database-backed caps for unfinished upload reservations."""

import uuid

import pytest
from fastapi import HTTPException

from apps.api.config import settings
from apps.api.models.asset import Asset, AssetType, AssetVersion, FileType, MediaFile, ProcessingStatus
from apps.api.models.project import Project, ProjectType
from apps.api.models.user import User
from apps.api.services.upload_limits import enforce_pending_upload_limits


def _pending(db, owner, project, size):
    asset = Asset(
        project_id=project.id, name="pending", asset_type=AssetType.video, created_by=owner.id
    )
    db.add(asset)
    db.flush()
    version = AssetVersion(
        asset_id=asset.id, version_number=1, processing_status=ProcessingStatus.uploading,
        created_by=owner.id,
    )
    db.add(version)
    db.flush()
    db.add(MediaFile(
        version_id=version.id, file_type=FileType.video, original_filename="pending.mp4",
        mime_type="video/mp4", file_size_bytes=size, s3_key_raw=f"raw/{version.id}",
    ))
    db.flush()


def _owner_project(db):
    owner = User(email=f"upload-limit-{uuid.uuid4()}@test.local", name="test")
    db.add(owner)
    db.flush()
    project = Project(name="upload limits", project_type=ProjectType.personal, created_by=owner.id)
    db.add(project)
    db.flush()
    return owner, project


def test_user_cannot_start_more_than_the_configured_number(real_db, monkeypatch):
    owner, project = _owner_project(real_db)
    _pending(real_db, owner, project, 1)
    monkeypatch.setattr(settings, "max_concurrent_uploads_per_user", 1)
    monkeypatch.setattr(settings, "max_pending_upload_bytes_per_project", 100)

    with pytest.raises(HTTPException) as error:
        enforce_pending_upload_limits(real_db, project.id, owner, 1)

    assert error.value.status_code == 429
    assert "maximum number" in error.value.detail


def test_project_quota_includes_new_upload_before_it_is_created(real_db, monkeypatch):
    owner, project = _owner_project(real_db)
    _pending(real_db, owner, project, 15)
    monkeypatch.setattr(settings, "max_concurrent_uploads_per_user", 3)
    monkeypatch.setattr(settings, "max_pending_upload_bytes_per_project", 20)

    with pytest.raises(HTTPException) as error:
        enforce_pending_upload_limits(real_db, project.id, owner, 6)

    assert error.value.status_code == 429
    assert "in-progress upload limit" in error.value.detail
