"""Service helper for creating ActivityLog entries."""

import uuid
from typing import Optional
from sqlalchemy.orm import Session

from ..models.activity import ActivityLog


def log_activity(
    db: Session,
    action: str,
    user_id: Optional[uuid.UUID] = None,
    org_id: Optional[uuid.UUID] = None,
    project_id: Optional[uuid.UUID] = None,
    asset_id: Optional[uuid.UUID] = None,
    payload: Optional[dict] = None,
) -> None:
    """Create an ActivityLog entry. Call before ``db.commit()``."""
    entry = ActivityLog(
        action=action,
        user_id=user_id,
        org_id=org_id,
        project_id=project_id,
        asset_id=asset_id,
        payload=payload or {},
    )
    db.add(entry)
