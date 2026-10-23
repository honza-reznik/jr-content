"""JR Content: exchange a Brightbean ticket for FreeFrame tokens (F5-B02).

Only Brightbean signs tickets (JRC_FF_SSO_SECRET); FreeFrame never issues them.
Every rejection is the same 401 so a caller learns nothing about the reason.
"""

import logging
import os
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db
from ..models.user import User, UserStatus
from ..services.auth_service import create_access_token, create_refresh_token
from ..services.jrc_ticket import (
    MAX_TICKET_LENGTH,
    TicketError,
    consume_nonce,
    sso_secret,
    verify_ticket,
)
from ..services.redis_service import get_redis

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])
INVALID_TICKET = "Invalid or expired ticket"


class JrcExchangeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ticket: str = Field(min_length=1, max_length=MAX_TICKET_LENGTH)


class JrcExchangeResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    # Only for the browser ("web"); a server ticket ("api") never gets a refresh token.
    refresh_token: str | None = None
    next: str | None = None


def _reject(reason: str, claims: dict | None = None):
    # Log the reason and the Brightbean account, never the ticket itself.
    account = claims.get("acct") if claims else None
    logger.warning("JR Content ticket rejected: %s (account %s)", reason, account)
    raise HTTPException(status_code=401, detail=INVALID_TICKET)


@router.post("/jrc-exchange", response_model=JrcExchangeResponse, response_model_exclude_none=True)
def jrc_exchange(body: JrcExchangeRequest, request: Request, response: Response, db: Session = Depends(get_db)):
    response.headers["Cache-Control"] = "no-store"
    secret = sso_secret()
    if secret is None:
        logger.error("JRC_FF_SSO_SECRET is missing or too short; JR Content sign-in is disabled.")
        raise HTTPException(status_code=503, detail="Single sign-on is not configured")
    try:
        claims = verify_ticket(body.ticket, secret=secret)
    except TicketError as exc:
        _reject(exc.reason)
    # A server ticket is redeemable only on the internal address (jrc-ff-api:8000).
    # The shared entrance (Caddy) always sets X-Real-IP; Brightbean's direct call never does.
    if claims["aud"] == "api" and "x-real-ip" in request.headers:
        _reject("server ticket used through the public entrance", claims)
    # Consume the nonce before anything else: the ticket is spent whatever happens next,
    # and two concurrent requests with the same ticket cannot both pass (SET NX).
    try:
        fresh = consume_nonce(get_redis(), claims["nonce"])
    except Exception:
        # Fail closed: without Redis single use cannot be guaranteed.
        logger.error("Redis unavailable; JR Content ticket refused.")
        raise HTTPException(status_code=503, detail="Sign-in is temporarily unavailable") from None
    if not fresh:
        _reject("ticket already used", claims)
    user = db.query(User).filter(User.id == uuid.UUID(claims["sub"])).first()
    service_email = os.environ.get("JRC_FF_SYNC_EMAIL", "").lower()
    if user is None:
        _reject("account does not exist", claims)
    if user.deleted_at is not None or user.status != UserStatus.active:
        _reject("account is deleted or not active", claims)
    if user.is_superadmin or (service_email and (user.email or "").lower() == service_email):
        _reject("privileged account", claims)
    result = {
        "access_token": create_access_token(str(user.id), token_version=user.token_version),
        "expires_in": settings.access_token_expire_minutes * 60,
    }
    if claims["aud"] == "web":
        result["refresh_token"] = create_refresh_token(str(user.id), token_version=user.token_version)
        result["next"] = claims.get("next")
    logger.info("JR Content ticket exchanged (%s) for account %s", claims["aud"], claims["acct"])
    return result
