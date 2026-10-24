from typing import Optional
from fastapi import Header, HTTPException, Depends, status
from sqlalchemy.orm import Session
from ..database import get_db
from ..models.share import ShareLink
from ..middleware.auth import get_optional_user
from ..models.user import User
from ..services.permissions import validate_share_link_with_session


def get_share_link(
    x_share_token: Optional[str] = Header(default=None),
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_optional_user),
) -> Optional[ShareLink]:
    """Optional share link dependency — returns None if no token provided."""
    if not x_share_token:
        return None
    return validate_share_link_with_session(db, x_share_token, current_user=current_user)


def require_share_link(
    x_share_token: Optional[str] = Header(default=None),
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_optional_user),
) -> ShareLink:
    """Required share link dependency — raises 401 if no token provided."""
    if not x_share_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Share token required")
    return validate_share_link_with_session(db, x_share_token, current_user=current_user)
