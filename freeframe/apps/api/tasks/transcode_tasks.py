import uuid
import sys
import os
import asyncio
import json
import logging
import psycopg2

# Ensure the workspace root is on the path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from celery.exceptions import Retry
from sqlalchemy.exc import DBAPIError, InterfaceError
from sqlalchemy import text

from .celery_app import celery_app
from ..database import SessionLocal
from ..models.asset import AssetVersion, MediaFile, ProcessingStatus, AssetType
from ..models.asset import Asset
from ..services.s3_service import get_s3_client
from ..config import settings

log = logging.getLogger("celery.transcode")


def _run_async(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _database_unavailable(exc):
    if not isinstance(exc, DBAPIError):
        return False
    # OperationalError also covers deadlocks, invalid SQL and permissions.
    # Only invalidated connections, SQLSTATE class 08 and server shutdowns
    # indicate an unavailable database (a failed connect has no SQLSTATE).
    code = getattr(exc.orig, "pgcode", None)
    return (exc.connection_invalidated or
            (code is not None and (code.startswith("08") or code in {"57P01", "57P02", "57P03"})) or
            (code is None and (isinstance(exc, InterfaceError) or
                               isinstance(exc.orig, psycopg2.OperationalError))))


_PENDING_FAILURES = "transcode:pending-failures"
# Latest conversion percent per version (JR Content P1-B26). Read by the version
# list so a client polling it can show progress; SSE alone reaches only an open
# FreeFrame page. Expires on its own if a worker dies mid-conversion.
_PROGRESS_KEY = "transcode:progress:{}"
_PROGRESS_TTL_SECONDS = 6 * 3600
_DATABASE_FAILURE = "Video conversion stopped after repeated database connection failures. Please upload the video again."


def _failure_store():
    from ..services.event_service import _get_sync_redis
    return _get_sync_redis()


def _record_progress(version_id, percent):
    """Best-effort: progress is information, never a reason to fail a conversion."""
    try:
        _failure_store().set(_PROGRESS_KEY.format(version_id), int(percent), ex=_PROGRESS_TTL_SECONDS)
        return True
    except Exception:
        log.warning("Could not record conversion progress for version %s", version_id)
        return False


def _clear_progress(version_id):
    try:
        _failure_store().delete(_PROGRESS_KEY.format(version_id))
    except Exception:
        log.warning("Could not clear conversion progress for version %s", version_id)


def _unreadable(exc):
    """A failure that says the uploaded bytes are not usable media (P1-B26).

    Such a file reads the same on every attempt, so retrying only delays the
    answer -- behind every other conversion in the single-slot queue. Anything
    not positively recognised stays retryable.
    """
    import subprocess
    from packages.transcoder.source_check import UnreadableSource, is_unreadable_source

    if isinstance(exc, UnreadableSource):
        return True
    if isinstance(exc, subprocess.CalledProcessError):
        stderr = exc.stderr
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", "replace")
        return is_unreadable_source(stderr or "")
    return False


def _close_session(db):
    # A rollback can be the first operation to discover a lost connection.
    # Neither it nor close may mask the original failure or prevent retrying.
    try:
        db.rollback()
    except Exception:
        log.warning("Could not roll back transcode session; discarding it")
    try:
        db.close()
    except Exception:
        log.warning("Could not close transcode session; invalidating it")
        try:
            db.invalidate()
        except Exception:
            log.warning("Could not invalidate transcode session")


def _write_failure(asset_id, version_id, error):
    """Use a fresh transaction, never an ORM object from the broken session."""
    db = SessionLocal()
    try:
        version = db.query(AssetVersion).filter(AssetVersion.id == uuid.UUID(version_id)).first()
        if version is None or version.processing_status == ProcessingStatus.ready:
            return
        asset = db.query(Asset).filter(Asset.id == uuid.UUID(asset_id)).first()
        version.processing_status = ProcessingStatus.failed
        db.commit()
        if asset is not None:
            _publish_event(str(asset.project_id), "transcode_failed", {
                "asset_id": asset_id, "error": error,
            })
    finally:
        _close_session(db)


def _record_failure(asset_id, version_id, error):
    try:
        _write_failure(asset_id, version_id, error)
    except DBAPIError:
        # The DB cannot accept a terminal status during an outage. Redis is
        # persistent in JR Content; keep only the status write pending, with
        # no TTL and no new conversion task or retry budget.
        _failure_store().hset(_PENDING_FAILURES, version_id, json.dumps({
            "asset_id": asset_id, "error": error,
        }))
        log.warning("Deferred terminal transcode status for version %s", version_id)


@celery_app.task(name="finish_failed_transcodes")
def finish_failed_transcodes():
    store = _failure_store()
    finished = 0
    for version_id, payload in store.hscan_iter(_PENDING_FAILURES):
        failure = json.loads(payload)
        # Leave the marker intact if the database is still unavailable.
        _write_failure(failure["asset_id"], version_id, failure["error"])
        store.hdel(_PENDING_FAILURES, version_id)
        finished += 1
    return finished


@celery_app.task(bind=True, max_retries=3, default_retry_delay=60, acks_late=True, reject_on_worker_lost=True)
def process_asset(self, asset_id: str, version_id: str):
    """Process an upload, retrying transactions interrupted by database outages."""
    db = SessionLocal()
    final_attempt = self.request.retries >= self.max_retries
    lock_connection = None
    lock_key = uuid.UUID(version_id).int % (2 ** 63)

    try:
        # Session lock survives ORM commits; a dedicated connection owns it.
        lock_connection = db.get_bind().connect()
        locked = lock_connection.execute(text("SELECT pg_try_advisory_lock(:key)"), {"key": lock_key}).scalar()
        lock_connection.commit()
        if not locked:
            return
        # These initial reads and the first commit used to bypass retrying.
        version = db.query(AssetVersion).filter(AssetVersion.id == uuid.UUID(version_id)).first()
        if not version or getattr(version, "deleted_at", None) is not None or version.processing_status in (ProcessingStatus.ready, ProcessingStatus.failed):
            return  # duplicate delivery, discarded version, or already finished

        asset = db.query(Asset).filter(Asset.id == uuid.UUID(asset_id)).first()
        if not asset:
            version.processing_status = ProcessingStatus.failed
            db.commit()
            return

        media_file = db.query(MediaFile).filter(MediaFile.version_id == version.id).first()
        if not media_file:
            version.processing_status = ProcessingStatus.failed
            db.commit()
            return

        version.processing_status = ProcessingStatus.processing
        db.commit()
        output_prefix = f"processed/{asset.project_id}/{asset_id}/{version_id}"
        s3 = get_s3_client()

        if asset.asset_type == AssetType.video:
            _process_video(
                db, asset, version, media_file, s3, output_prefix,
                final_attempt=final_attempt,
            )
        elif asset.asset_type == AssetType.audio:
            _process_audio(db, asset, version, media_file, s3, output_prefix)
        elif asset.asset_type in (AssetType.image, AssetType.image_carousel):
            _process_image(db, asset, version, media_file, s3, output_prefix)

        version.processing_status = ProcessingStatus.ready
        db.commit()
        _clear_progress(version_id)
        _publish_event(str(asset.project_id), "transcode_complete", {
            "asset_id": asset_id,
            "version_id": version_id,
        })
    except Exception as exc:
        _close_session(db)
        db = None
        from packages.transcoder.redact import redact_presigned_urls

        error = _DATABASE_FAILURE if _database_unavailable(exc) else redact_presigned_urls(str(exc))
        _clear_progress(version_id)
        if _unreadable(exc):
            # Permanent: record it now, without the retries meant for a failed read.
            log.warning("Version %s is not usable media; failing without retry: %s", version_id, error)
            _record_failure(asset_id, version_id, error)
            return
        if final_attempt:
            _record_failure(asset_id, version_id, error)
            raise
        try:
            raise self.retry(exc=exc)
        except Retry:
            raise  # Scheduled: keep processing until the next attempt.
        except Exception:
            # The broker refused the retry: record a terminal status so the
            # upload cannot remain at Converting with no queued work.
            _record_failure(asset_id, version_id, error)
            raise
    finally:
        if db is not None:
            db.close()
        if lock_connection is not None:
            try:
                lock_connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": lock_key})
                lock_connection.commit()
            except Exception:
                # Never return a connection holding a session lock to the pool.
                lock_connection.invalidate()
                log.warning("Discarded conversion lock connection")
            finally:
                lock_connection.close()


def _process_video(
    db, asset, version, media_file, s3, output_prefix, *,
    final_attempt: bool = False,
):
    from packages.transcoder.ffmpeg_transcoder import FFmpegTranscoder, parse_qualities
    from packages.transcoder.base import TranscodeJob

    transcoder = FFmpegTranscoder(s3, settings.s3_bucket, settings.s3_endpoint)
    # Progress is cosmetic; the transcode is not. If the broker stops answering,
    # every publish costs the socket timeout, and this runs inside the loop
    # draining ffmpeg's stdout -- so paying it ~100 times would stall the encode
    # behind a full pipe. One failure is enough to stop trying for this job.
    progress_ok = {"enabled": True, "high_water": -1}
    # Started, 0 %: distinguishes a running conversion from one still queued.
    _record_progress(str(version.id), 0)

    def _on_progress(percent: int) -> None:
        if not progress_ok["enabled"]:
            return
        # A hardware attempt that fails at runtime is retried on the software
        # backend, and the second ffmpeg starts counting from zero. Clamp so the
        # bar never runs backwards from the viewer's point of view.
        if percent <= progress_ok["high_water"]:
            return
        progress_ok["high_water"] = percent
        recorded = _record_progress(str(version.id), percent)
        delivered = _publish_event(str(asset.project_id), "transcode_progress", {
            "asset_id": str(asset.id),
            "percent": percent,
        })
        if delivered is False or not recorded:
            progress_ok["enabled"] = False

    job = TranscodeJob(
        media_id=str(asset.id),
        version_id=str(version.id),
        input_s3_key=media_file.s3_key_raw,
        output_s3_prefix=output_prefix,
        qualities=parse_qualities(settings.transcoder_qualities),
        progress_cb=_on_progress,
        final_attempt=final_attempt,
    )
    result = _run_async(transcoder.transcode(job))

    if result.no_video_stream:
        # The browser types a .mpg or .mp4 carrying only audio as video/*, so
        # mime_to_asset_type routed it here. The file is perfectly good, it just
        # is not video. Correct the asset and run it through the audio pipeline
        # rather than failing an upload the user had no reason to think was
        # wrong (#82).
        log.info(
            "asset %s has no video stream; re-routing to the audio pipeline",
            asset.id,
        )
        asset.asset_type = AssetType.audio
        db.flush()
        _process_audio(db, asset, version, media_file, s3, output_prefix)
        return

    if not result.success:
        if result.unreadable_source:
            from packages.transcoder.source_check import UnreadableSource
            raise UnreadableSource(f"Transcode failed: {result.error}")
        raise RuntimeError(f"Transcode failed: {result.error}")

    media_file.s3_key_processed = result.hls_prefix
    # None when the remux failed. Recorded either way so a re-run of a version
    # that once had a download rung and now does not stops advertising it.
    media_file.s3_key_download = result.mp4_key
    if result.thumbnail_keys:
        media_file.s3_key_thumbnail = result.thumbnail_keys[0]
    if result.duration_seconds:
        media_file.duration_seconds = result.duration_seconds
    if result.width:
        media_file.width = result.width
    if result.height:
        media_file.height = result.height
    if result.fps:
        media_file.fps = result.fps
    db.flush()


def _process_audio(db, asset, version, media_file, s3, output_prefix):
    from packages.transcoder.image_processor import process_audio
    result = process_audio(s3, settings.s3_bucket, media_file.s3_key_raw, output_prefix)
    media_file.s3_key_processed = result.get("mp3_key")
    if result.get("waveform_key"):
        media_file.s3_key_thumbnail = result["waveform_key"]
    if result.get("duration_seconds"):
        media_file.duration_seconds = result["duration_seconds"]
    db.flush()


def _process_image(db, asset, version, media_file, s3, output_prefix):
    from packages.transcoder.image_processor import process_image
    result = process_image(s3, settings.s3_bucket, media_file.s3_key_raw, output_prefix)
    media_file.s3_key_processed = result.get("webp_key")
    media_file.s3_key_thumbnail = result.get("thumbnail_key")
    db.flush()


def _publish_event(project_id: str, event_type: str, payload: dict):
    """Publish an SSE event from Celery worker context.

    Delegates to event_service so there is a single emit path; it is pooled and
    already best-effort, which is what this needs.
    """
    from ..services import event_service
    return event_service.publish_sync(project_id, event_type, payload)


def _eligible_media_rows(db):
    """Rows the #124 backfill still needs: (MediaFile, asset_type) pairs."""
    return (
        db.query(MediaFile, Asset.asset_type)
        .join(AssetVersion, MediaFile.version_id == AssetVersion.id)
        .join(Asset, AssetVersion.asset_id == Asset.id)
        .filter(
            AssetVersion.processing_status == ProcessingStatus.ready,
            AssetVersion.deleted_at.is_(None),
            Asset.deleted_at.is_(None),
            MediaFile.duration_seconds.is_(None),
            Asset.asset_type.in_([AssetType.video, AssetType.audio]),
        )
        .all()
    )


@celery_app.task(bind=True)
def backfill_media_metadata(self):
    """One-off backfill for #124: probe raw S3 files to populate missing
    duration/width/height/fps on already-processed media. Idempotent —
    only touches rows where duration_seconds IS NULL."""
    import subprocess
    from packages.transcoder.ffmpeg_transcoder import (
        _PRIMARY_VIDEO_STREAM,
        parse_probe_metadata,
    )
    from packages.transcoder.redact import redact_presigned_urls

    db = SessionLocal()
    updated = skipped = 0
    try:
        rows = _eligible_media_rows(db)
        s3 = get_s3_client()
        for media_file, asset_type in rows:
            row_id = media_file.id
            try:
                url = s3.generate_presigned_url(
                    "get_object",
                    Params={"Bucket": settings.s3_bucket, "Key": media_file.s3_key_raw},
                    ExpiresIn=3600,
                )
                cmd = ["ffprobe", "-v", "error", "-print_format", "json", "-show_format"]
                if asset_type == AssetType.video:
                    cmd += ["-show_streams", "-select_streams", _PRIMARY_VIDEO_STREAM]
                probe = subprocess.run(cmd + [url], capture_output=True, text=True, timeout=300)
                if probe.returncode != 0:
                    skipped += 1
                    continue
                try:
                    data = json.loads(probe.stdout)
                except json.JSONDecodeError:
                    skipped += 1
                    continue
                if asset_type == AssetType.video:
                    meta = parse_probe_metadata(data)
                    if meta is None:
                        skipped += 1
                        continue
                    media_file.duration_seconds = meta.duration_seconds or None
                    media_file.width = meta.width or None
                    media_file.height = meta.height or None
                    media_file.fps = meta.fps or None
                else:
                    duration = float((data.get("format") or {}).get("duration") or 0)
                    media_file.duration_seconds = duration or None
                db.commit()
                updated += 1
            except Exception as exc:
                db.rollback()
                skipped += 1
                # A timeout quotes the probe's argv, presigned URL included.
                log.warning(
                    "backfill: skipping media_file %s: %s",
                    row_id, redact_presigned_urls(str(exc)),
                )
                continue
        return {"updated": updated, "skipped": skipped}
    finally:
        db.close()
