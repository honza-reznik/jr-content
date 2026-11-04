"""Skutečný zápis komentáře, výpadek a ztracené potvrzení; vlastní Docker projekt."""
import json
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import httpx
from apps.api.database import SessionLocal
from apps.api.models.asset import Asset, AssetType, AssetVersion, ProcessingStatus
from apps.api.models.comment import Comment
from apps.api.models.jrc_comment_event import JrcCommentEvent
from apps.api.models.user import User
from apps.api.services.auth_service import create_access_token
from apps.api.services.jrc_comment_notifications import deliver_next_jrc_comment

assert os.environ.get("JRC_PROJECT") == "jrc-p1b13"
fixture = json.load(sys.stdin)
with SessionLocal() as db, db.begin():
    user = db.get(User, uuid.UUID(fixture["ff_client"]))
    token = create_access_token(str(user.id), token_version=user.token_version)
    if db.get(Asset, uuid.UUID(fixture["asset"])) is None:
        asset = Asset(id=uuid.UUID(fixture["asset"]), project_id=uuid.UUID(fixture["project"]), name="Review recovery video", asset_type=AssetType.video, created_by=user.id)
        db.add(asset)
        db.flush()
        db.add(AssetVersion(id=uuid.UUID(fixture["version"]), asset_id=asset.id, version_number=1, processing_status=ProcessingStatus.ready, created_by=asset.created_by))
response = httpx.post(
    f'http://jrc-ff-api:8000/assets/{fixture["asset"]}/comments',
    headers={"Authorization": "Bearer " + token},
    json={"version_id": fixture["version"], "body": "Review 1 durable client comment", "timecode_start": 17}, timeout=20,
)
assert response.status_code == 201, response.status_code
comment_id = uuid.UUID(response.json()["id"])
with SessionLocal() as db, db.begin():
    assert db.get(Comment, comment_id).body == "Review 1 durable client comment"
    assert db.get(JrcCommentEvent, comment_id).delivered_at is None
    with patch("apps.api.services.jrc_comment_notifications.httpx.post", side_effect=httpx.ConnectError("simulated local outage")):
        assert deliver_next_jrc_comment(db)
# New DB session demonstrates durable recovery, independent of the API request.
with SessionLocal() as db, db.begin():
    event = db.get(JrcCommentEvent, comment_id)
    assert event.attempts == 1 and event.delivered_at is None
    event.next_attempt_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db.flush()
    real_post = httpx.post

    def lose_ack(*args, **kwargs):
        response = real_post(*args, **kwargs)
        assert response.status_code == 204, response.status_code
        raise httpx.ReadTimeout("simulated lost acknowledgement after receiver committed")

    with patch("apps.api.services.jrc_comment_notifications.httpx.post", side_effect=lose_ack):
        assert deliver_next_jrc_comment(db)
    assert event.attempts == 2 and event.delivered_at is None
    # The real maintenance worker must recover this pending attempt on restart.
    event.next_attempt_at = datetime.now(timezone.utc) - timedelta(seconds=1)
print("Komentář 201 a událost přežily odmítnuté spojení i ztracené potvrzení; čeká automatické opakování.")
