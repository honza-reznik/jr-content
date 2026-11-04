"""Deliver managed video comment events to the local JR Content notification inbox."""
import hashlib
import hmac
import json
import logging
import os
import time

import httpx

log = logging.getLogger(__name__)
CONTEXT = b"jrc-video-comment.v1."


def notify_jrc_comment(comment, user):
    secret = os.environ.get("JRC_FF_SSO_SECRET", "")
    if user.jrc_managed is not True or len(secret) < 32 or comment.visibility != "public":
        return
    payload = json.dumps({
        "comment_id": str(comment.id), "asset_id": str(comment.asset_id),
        "author_id": str(user.id), "timestamp": int(time.time()),
    }, sort_keys=True).encode()
    signature = hmac.new(secret.encode(), CONTEXT + payload, hashlib.sha256).hexdigest()
    try:
        response = httpx.post(
            "http://jrc-bb-web:8000/video/comment-events/", data=payload,
            headers={"X-JRC-Signature": signature, "Content-Type": "application/json"},
            timeout=3, follow_redirects=False, trust_env=False,
        )
        response.raise_for_status()
    except httpx.HTTPError:
        log.warning("Could not deliver JR Content video comment notification %s", comment.id)
