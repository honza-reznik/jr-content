from pydantic import BaseModel
import uuid
from datetime import datetime
from typing import Optional


class ActivityLogResponse(BaseModel):
    id: uuid.UUID
    action: str
    user_id: Optional[uuid.UUID]
    org_id: Optional[uuid.UUID]
    project_id: Optional[uuid.UUID]
    asset_id: Optional[uuid.UUID]
    payload: dict
    created_at: datetime
    model_config = {"from_attributes": True}
