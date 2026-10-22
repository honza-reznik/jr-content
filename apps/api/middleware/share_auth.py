from typing import Optional
from fastapi import Header, HTTPException, Depends, status
from sqlalchemy.orm import Session
from ..database import get_db
from ..models.share import ShareLink
from ..services.permissions import validate_share_link


def get_share_link(
    x_share_token: Optional[str] = Header(default=None),
    db: Session = Depends(get_db),
) -> Optional[ShareLink]:
    """Optional share link dependency — returns None if no token provided."""
    if not x_share_token:
        return None
    return validate_share_link(db, x_share_token)


def require_share_link(
    x_share_token: Optional[str] = Header(default=None),
    db: Session = Depends(get_db),
) -> ShareLink:
    """Required share link dependency — raises 401 if no token provided."""
    if not x_share_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Share token required")
    return validate_share_link(db, x_share_token)
