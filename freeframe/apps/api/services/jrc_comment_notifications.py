"""Durable local notification delivery; Brightbean deduplicates by comment ID."""

import hashlib
import hmac
import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone

import httpx

from ..models.jrc_comment_event import JrcCommentEvent

log = logging.getLogger(__name__)
CONTEXT = b"jrc-video-comment.v1."


def queue_jrc_comment(db, comment, user) -> bool:
    """Called before the comment transaction commits, even if delivery is unavailable."""
    if user is None or user.jrc_managed is not True or comment.visibility != "public":
        return False
    db.add(
        JrcCommentEvent(
            comment_id=comment.id, asset_id=comment.asset_id, author_id=user.id
        )
    )
    return True


def deliver_next_jrc_comment(db):
    """Attempt one due event under a row lock; the caller commits or rolls back."""
    now = datetime.now(timezone.utc)
    event = (
        db.query(JrcCommentEvent)
        .filter(
            JrcCommentEvent.delivered_at.is_(None),
            JrcCommentEvent.next_attempt_at <= now,
        )
        .order_by(JrcCommentEvent.next_attempt_at, JrcCommentEvent.comment_id)
        .with_for_update(skip_locked=True)
        .first()
    )
    if event is None:
        return False
    event.attempts += 1
    # Retry indefinitely with bounded backoff. The receiver's transactional
    # receipt prevents duplicates after a lost HTTP acknowledgement.
    event.next_attempt_at = now + timedelta(
        seconds=min(30 * 2 ** min(event.attempts - 1, 7), 3600)
    )
    secret = os.environ.get("JRC_FF_SSO_SECRET", "")
    if len(secret) < 32:
        log.warning("JR Content comment delivery key is unavailable")
        return True
    # Never persist timestamps or signatures: every attempt must be fresh.
    payload = json.dumps(
        {
            "comment_id": str(event.comment_id),
            "asset_id": str(event.asset_id),
            "author_id": str(event.author_id),
            "timestamp": int(time.time()),
        },
        sort_keys=True,
    ).encode()
    signature = hmac.new(secret.encode(), CONTEXT + payload, hashlib.sha256).hexdigest()
    try:
        response = httpx.post(
            "http://jrc-bb-web:8000/video/comment-events/",
            content=payload,
            headers={"X-JRC-Signature": signature, "Content-Type": "application/json"},
            timeout=3,
            follow_redirects=False,
            trust_env=False,
        )
        response.raise_for_status()
    except httpx.HTTPError:
        log.warning(
            "Could not deliver JR Content video comment notification %s",
            event.comment_id,
        )
    else:
        event.delivered_at = datetime.now(timezone.utc)
    return True
