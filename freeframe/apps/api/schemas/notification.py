from pydantic import BaseModel
import uuid
from datetime import datetime
from typing import Optional
from ..models.activity import NotificationType


class NotificationResponse(BaseModel):
    id: uuid.UUID
    type: NotificationType
    asset_id: uuid.UUID
    comment_id: Optional[uuid.UUID]
    read: bool
    created_at: datetime
    model_config = {"from_attributes": True}


class AssignmentUpdate(BaseModel):
    assignee_id: Optional[uuid.UUID] = None
    due_date: Optional[datetime] = None
