"""Transactional outbox: an API restart or a broker failure cannot lose a conversion."""
import logging
from ..database import SessionLocal
from ..models.asset import AssetVersion, ProcessingStatus
from ..models.pending_transcode import PendingTranscode
from .celery_app import celery_app

log = logging.getLogger(__name__)


def dispatch_pending(version_id=None):
    from .transcode_tasks import process_asset
    # Lock each marker until the publish is acknowledged. A crash after publish
    # can duplicate delivery; process_asset serializes a version and skips ready ones.
    with SessionLocal() as db:
        query = db.query(PendingTranscode).order_by(PendingTranscode.created_at)
        if version_id is not None:
            query = query.filter(PendingTranscode.version_id == version_id)
        pending = query.with_for_update(skip_locked=True).limit(50).all()
        for marker in pending:
            version = db.query(AssetVersion).filter(AssetVersion.id == marker.version_id).first()
            if version is None or version.deleted_at is not None or version.processing_status != ProcessingStatus.processing:
                db.delete(marker)
                continue
            try:
                # Synchronous, bounded broker publishing. No daemon thread.
                process_asset.apply_async(args=[str(version.asset_id), str(version.id)], retry=False)
            except Exception:
                marker.attempts += 1
                if marker.attempts >= 3:
                    version.processing_status = ProcessingStatus.failed
                    db.delete(marker)
                log.warning("Conversion dispatch deferred for version %s", marker.version_id)
            else:
                db.delete(marker)
        db.commit()
        return len(pending)


@celery_app.task(name="dispatch_pending_transcodes", queue="maintenance")
def dispatch_pending_transcodes():
    return dispatch_pending()
