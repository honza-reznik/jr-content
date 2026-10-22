from pydantic import BaseModel
import uuid
from datetime import datetime
from typing import Optional
from ..models.approval import ApprovalStatus

class ApprovalCreate(BaseModel):
    version_id: uuid.UUID
    note: Optional[str] = None

class ApprovalResponse(BaseModel):
    id: uuid.UUID
    asset_id: uuid.UUID
    version_id: uuid.UUID
    user_id: uuid.UUID
    status: ApprovalStatus
    note: Optional[str]
    created_at: datetime
    model_config = {"from_attributes": True}
