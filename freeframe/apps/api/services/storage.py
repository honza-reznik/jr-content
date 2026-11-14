from sqlalchemy import exists, func
from sqlalchemy.orm import Session, aliased

from ..models.asset import Asset, AssetVersion, MediaFile, ProcessingStatus
from ..models.instance_settings import InstanceSettings
from ..schemas.upload import _format_bytes, part_count_error, part_size_for, upload_size_error


def get_storage_limit(db: Session) -> int:
    """Non-creating read of the instance storage cap. 0 (or no row) == unlimited."""
    row = db.query(InstanceSettings).first()
    return row.storage_limit_bytes if row else 0


def get_upload_part_size(db: Session) -> int:
    """Non-creating read of the upload part size setting. 0 (or no row) == automatic."""
    row = db.query(InstanceSettings).first()
    return row.upload_part_size_bytes if row else 0


def get_allow_original_downloads(db: Session) -> bool:
    """Non-creating read: may people outside a project download originals? No row == no."""
    row = db.query(InstanceSettings).first()
    return bool(row.allow_original_downloads) if row else False


def choose_part_size(db: Session, file_size_bytes: int) -> tuple[int, str | None]:
    """The part size a new upload is cut on, and why it cannot be uploaded at all
    (a fixed size too small for 10,000 parts to hold the file), or None."""
    configured = get_upload_part_size(db)
    part_size = part_size_for(file_size_bytes, configured)
    return part_size, part_count_error(file_size_bytes, part_size, fixed=bool(configured))


_COMMITTED = (ProcessingStatus.processing, ProcessingStatus.ready)


def counted_once():
    """Predicate on MediaFile: count stored bytes once per object (JR Content P2-B15).

    A version used for another output is a second MediaFile naming the same original; its bytes are
    stored once, so only the lowest live committed row naming an original counts it. When that row
    goes, the next one counts, so the figure never drops while any row still holds the file.
    """
    other, other_version, other_asset = aliased(MediaFile), aliased(AssetVersion), aliased(Asset)
    return ~exists().where(
        other.s3_key_raw == MediaFile.s3_key_raw,
        other.id < MediaFile.id,
        other_version.id == other.version_id,
        other_asset.id == other_version.asset_id,
        other_asset.deleted_at.is_(None),
        other_version.deleted_at.is_(None),
        other_version.processing_status.in_(_COMMITTED),
    )


def _committed_media_sum(db: Session):
    """Base query summing MediaFile.file_size_bytes over *committed* media only — the single
    definition of "used storage" shared by the instance-wide and per-project sums.

    Counts only bytes actually in S3: versions in `processing`/`ready`, excluding soft-deleted
    assets/versions. MediaFile has no deleted_at, so soft-delete is excluded via the joins.
    Callers add their own scope (e.g. a project filter) before `.scalar()`.
    """
    return db.query(func.coalesce(func.sum(MediaFile.file_size_bytes), 0)) \
        .join(AssetVersion, MediaFile.version_id == AssetVersion.id) \
        .join(Asset, AssetVersion.asset_id == Asset.id) \
        .filter(
            Asset.deleted_at.is_(None),
            AssetVersion.deleted_at.is_(None),
            AssetVersion.processing_status.in_(
                [ProcessingStatus.processing, ProcessingStatus.ready]
            ),
            counted_once(),
        )


def instance_storage_used_bytes(db: Session) -> int:
    """Instance-wide committed storage in bytes — the single source of truth for both the
    member GET indicator and cap enforcement."""
    return _committed_media_sum(db).scalar() or 0


def project_storage_used_bytes(db: Session, project_id) -> int:
    """Committed storage in bytes for a single project — same semantics as the instance-wide
    figure, so per-project and instance usage agree."""
    return _committed_media_sum(db).filter(Asset.project_id == project_id).scalar() or 0


def storage_cap_error(db: Session, incoming_bytes: int) -> str | None:
    """Return an error detail if accepting `incoming_bytes` would exceed the instance
    storage cap, else None. Limit 0 (unlimited) short-circuits before any usage query.
    """
    limit = get_storage_limit(db)
    if not limit:
        return None
    used = instance_storage_used_bytes(db)
    if used + incoming_bytes > limit:
        return f"Storage limit reached — {_format_bytes(used)} of {_format_bytes(limit)} used"
    return None


def upload_guard_error(db: Session, file_size_bytes: int) -> str | None:
    """Combined pre-upload guard: the per-file cap (env `MAX_UPLOAD_BYTES`) first, then the
    instance-wide storage cap (DB). Returns the first error message, or None if both pass."""
    return upload_size_error(file_size_bytes) or storage_cap_error(db, file_size_bytes)
