"""Live project events over Server-Sent Events.

Access is not decided once at connect. A stream can stay open for hours, so it
re-checks for as long as it is open (see event_service.event_stream) and ends
with a final `revoked` event once its subscriber may no longer see the project.
"""
import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from fastapi.responses import StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

from ..config import settings
from ..database import SessionLocal, get_db
from ..middleware.auth import get_current_user, optional_bearer_scheme
from ..models.user import User
from ..services.auth_service import decode_token
from ..services.event_service import event_stream
from ..services.permissions import EVENTS_ACCESS_REVOKED, project_events_denial

router = APIRouter(prefix="/events", tags=["events"])
SSE_TICKET_SECONDS = 300


def _access_claims(*tokens: Optional[str]) -> Optional[tuple[uuid.UUID, int, float]]:
    """(user id, token version, expiry) from the first valid access token given."""
    for raw in tokens:
        if not raw:
            continue
        payload = decode_token(raw)
        if not payload or payload.get("type") != "access":
            continue
        try:
            return uuid.UUID(str(payload["sub"])), int(payload.get("ver", 1)), float(payload["exp"])
        except (KeyError, TypeError, ValueError):
            continue
    return None


def _denial(project_id: uuid.UUID, user_id: uuid.UUID, token_version: int) -> Optional[str]:
    """One authorization check on its own short-lived session.

    A request-scoped session would stay checked out for the life of the
    stream, so each check opens and closes its own. Token expiry is not
    checked here: decode_token() refuses an expired token at connect, and
    event_stream compares `exp` before every write.
    """
    db = SessionLocal()
    try:
        return project_events_denial(db, project_id, user_id, token_version)
    finally:
        db.close()


@router.post("/{project_id}/ticket")
def issue_event_ticket(
    project_id: uuid.UUID,
    response: Response,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Issue a short-lived, project-bound credential for native EventSource."""
    denial = project_events_denial(db, project_id, current_user.id, current_user.token_version)
    if denial is not None:
        detail = "Not allowed" if denial == EVENTS_ACCESS_REVOKED else "Not authenticated"
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail)
    response.headers["Cache-Control"] = "no-store"
    token = jwt.encode({
        "sub": str(current_user.id),
        "type": "sse",
        "pid": str(project_id),
        "ver": current_user.token_version,
        "exp": datetime.now(timezone.utc) + timedelta(seconds=SSE_TICKET_SECONDS),
    }, settings.jwt_secret, algorithm=settings.jwt_algorithm)
    return {"token": token}


def _ticket_claims(raw: str, project_id: uuid.UUID) -> Optional[tuple[uuid.UUID, int, float]]:
    try:
        payload = jwt.decode(raw, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
        if payload.get("type") != "sse" or payload.get("pid") != str(project_id):
            return None
        return uuid.UUID(str(payload["sub"])), int(payload["ver"]), float(payload["exp"])
    except (jwt.InvalidTokenError, KeyError, TypeError, ValueError):
        return None


@router.get("/{project_id}")
async def stream_events(
    project_id: uuid.UUID,
    # The query token is a short-lived, project-bound SSE ticket. Login JWTs
    # are accepted only in the Authorization header and never in the URL.
    token: Optional[str] = Query(None),
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(optional_bearer_scheme),
):
    claims = (
        _access_claims(credentials.credentials)
        if credentials and credentials.credentials else None
    )
    if claims is None and token:
        claims = _ticket_claims(token, project_id)
    if claims is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authenticated")
    user_id, token_version, expires_at = claims

    async def authorize() -> Optional[str]:
        # The query is synchronous; keep it off the event loop.
        return await asyncio.to_thread(_denial, project_id, user_id, token_version)

    # Refuse here rather than open the stream and revoke it: a client resets its
    # reconnect backoff when a stream opens, so open-then-revoke would invite a
    # fast reconnect loop.
    checked_at = asyncio.get_running_loop().time()
    denial = await authorize()
    if denial is not None:
        detail = "Not allowed" if denial == EVENTS_ACCESS_REVOKED else "Not authenticated"
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail)

    return StreamingResponse(
        event_stream(
            str(project_id),
            authorize,
            max_auth_age_seconds=settings.sse_auth_max_age_seconds,
            recheck_seconds=settings.sse_auth_recheck_seconds,
            expires_at=expires_at,
            checked_at=checked_at,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
