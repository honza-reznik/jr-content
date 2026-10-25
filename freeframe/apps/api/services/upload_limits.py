"""Database-backed limits for uploads that are still being transferred."""

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..config import settings
from ..models.asset import Asset, AssetVersion, MediaFile, ProcessingStatus
from ..models.project import Project
from ..models.user import User


def enforce_pending_upload_limits(db: Session, project_id, user: User, incoming_bytes: int) -> None:
    """Serialize upload starts and reject work that exceeds either configured cap.

    Row locks make the check and the later insertion in the same request one
    reservation: starts by the same user cannot race the user cap, and starts
    in one project cannot race the project byte cap.
    """
    locked_user = db.query(User).filter(User.id == user.id).with_for_update().first()
    project = db.query(Project).filter(Project.id == project_id).with_for_update().first()
    if locked_user is None or project is None:
        raise HTTPException(status_code=404, detail="Project not found")

    pending_user_count = (
        db.query(func.count(AssetVersion.id))
        .join(Asset, Asset.id == AssetVersion.asset_id)
        .filter(
            AssetVersion.created_by == user.id,
            AssetVersion.processing_status == ProcessingStatus.uploading,
            AssetVersion.deleted_at.is_(None),
            Asset.deleted_at.is_(None),
        )
        .scalar()
        or 0
    )
    if pending_user_count >= settings.max_concurrent_uploads_per_user:
        raise HTTPException(
            status_code=429,
            detail="You already have the maximum number of uploads in progress.",
            headers={"Retry-After": "60"},
        )

    pending_project_bytes = (
        db.query(func.coalesce(func.sum(MediaFile.file_size_bytes), 0))
        .join(AssetVersion, AssetVersion.id == MediaFile.version_id)
        .join(Asset, Asset.id == AssetVersion.asset_id)
        .filter(
            Asset.project_id == project_id,
            AssetVersion.processing_status == ProcessingStatus.uploading,
            AssetVersion.deleted_at.is_(None),
            Asset.deleted_at.is_(None),
        )
        .scalar()
        or 0
    )
    if pending_project_bytes + incoming_bytes > settings.max_pending_upload_bytes_per_project:
        raise HTTPException(
            status_code=429,
            detail="This project has reached its in-progress upload limit.",
            headers={"Retry-After": "60"},
        )


