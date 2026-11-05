"""Periodic outbox sweep also recovers failed broker dispatch and worker restarts."""

from ..database import SessionLocal
from ..services.jrc_comment_notifications import deliver_next_jrc_comment
from .celery_app import celery_app


@celery_app.task(name="deliver_jrc_comment_events", queue="maintenance")
def deliver_jrc_comment_events():
    for _ in range(50):
        with SessionLocal() as db, db.begin():
            if not deliver_next_jrc_comment(db):
                break
