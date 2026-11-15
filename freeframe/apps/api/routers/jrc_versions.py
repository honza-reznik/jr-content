"""JR Content (P2-B15): use a converted video version for another output without copying the file."""
import uuid
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..database import get_db
from ..middleware.auth import get_current_user
from ..middleware.rate_limit import user_rate_limit
from ..models.user import User
from ..services.jrc_duplicate import duplicate_version

router = APIRouter(prefix="/jrc", tags=["jrc"])


class DuplicateVersionRequest(BaseModel):
    # An existing target video gets a new version; without it a new video named `asset_name`.
    asset_id: Optional[uuid.UUID] = None
    asset_name: Optional[str] = Field(default=None, max_length=255)
    copy_comments: bool = False


class DuplicateVersionResponse(BaseModel):
    asset_id: uuid.UUID
    version_id: uuid.UUID
    version_number: int
    comments_copied: int


@router.post(
    "/versions/{version_id}/duplicate",
    response_model=DuplicateVersionResponse,
    dependencies=[Depends(user_rate_limit("jrc_version_duplicate", 20, 60, fail_closed=True))],
)
def duplicate(
    version_id: uuid.UUID,
    body: DuplicateVersionRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """The new version shares the source's stored files and is ready at once (no conversion)."""
    result = duplicate_version(
        db, current_user, version_id,
        target_asset_id=body.asset_id, asset_name=body.asset_name, copy_comments=body.copy_comments,
    )
    db.commit()
    return result
