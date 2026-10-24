from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import uuid
from typing import Optional
from sqlalchemy.orm import Session
from ..database import get_db
from ..services.auth_service import decode_token, get_user_by_id
from ..models.user import User, UserStatus

bearer_scheme = HTTPBearer()
optional_bearer_scheme = HTTPBearer(auto_error=False)

def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    token = credentials.credentials
    payload = decode_token(token)
    if not payload or payload.get("type") != "access":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")
    try:
        user = get_user_by_id(db, uuid.UUID(payload["sub"]))
    except (KeyError, ValueError, TypeError):
        raise HTTPException(status_code=401, detail="Invalid or expired token") from None
    if not user or user.status == UserStatus.deactivated:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found or deactivated")
    if payload.get("ver", 1) != user.token_version:
        raise HTTPException(status_code=401, detail="Session expired, please log in again")
    return user

def get_optional_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(optional_bearer_scheme),
    db: Session = Depends(get_db),
) -> Optional[User]:
    """Allow guests without credentials; reject an invalid supplied session with 401.

    Share clients use that status to refresh an expired access token. Treating
    it as anonymous would turn expiration into a membership denial (403).
    """
    if not credentials:
        return None
    return get_current_user(credentials, db)

