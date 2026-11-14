"""JR Content (P2-B15): one uploaded video for another output, without copying the file.

A JR Content output (channel + format) has its own FreeFrame asset with its own versions. Using a
converted version for another output adds a version to the target asset (or creates the asset)
whose MediaFile names the *same* storage objects as the source: the original, the HLS prefix, the
download MP4 and the thumbnail. Nothing is copied or converted again; the new version is ready at
once. The objects are shared from then on and the cleanup deletes one only with the last row that
names it (``tasks.cleanup_tasks.media_key_still_referenced``). Both branches then evolve on their
own: new versions, approvals and comments of one asset never touch the other.

Optionally the source version's comments are copied to the new version as they are: threads,
visibility (team only / team and client), authors (members and guests), timecodes, times, resolved
state, drawings, reactions and attachments (the attachment rows name the same objects as well).
Copies notify nobody: nothing new was said.
"""
import uuid

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models.asset import Asset, AssetType, AssetVersion, MediaFile, ProcessingStatus
from ..models.comment import Annotation, Comment, CommentAttachment, CommentReaction
from ..models.project import ProjectRole
from ..models.user import User
from .activity_service import log_activity
from .permissions import require_project_role
from .versions import next_version_number

# Every column of a MediaFile except its identity and the version it belongs to.
_MEDIA_COPY_COLUMNS = (
    "file_type", "original_filename", "mime_type", "file_size_bytes", "s3_key_raw", "s3_key_processed",
    "s3_key_download", "s3_key_thumbnail", "width", "height", "duration_seconds", "fps", "sequence_order",
)


def _live_asset(db: Session, asset_id, *, lock=False) -> Asset | None:
    query = db.query(Asset).filter(Asset.id == asset_id, Asset.deleted_at.is_(None))
    return (query.with_for_update() if lock else query).first()


def duplicate_version(
    db: Session,
    user: User,
    source_version_id: uuid.UUID,
    *,
    target_asset_id: uuid.UUID | None = None,
    asset_name: str | None = None,
    copy_comments: bool = False,
) -> dict:
    """Add a version sharing the source version's files to the target asset. Does not commit."""
    # FOR SHARE on the source: a concurrent delete of the version waits for this copy.
    source = db.query(AssetVersion).filter(
        AssetVersion.id == source_version_id, AssetVersion.deleted_at.is_(None),
    ).with_for_update(read=True).first()
    source_asset = _live_asset(db, source.asset_id) if source else None
    if source is None or source_asset is None:
        raise HTTPException(404, "Version not found")
    # The same right as uploading a version; FreeFrame checks the caller's own project role.
    require_project_role(db, source_asset.project_id, user, ProjectRole.editor)
    if source_asset.asset_type != AssetType.video:
        raise HTTPException(400, "Only a video version can be used for another output")
    if source.processing_status != ProcessingStatus.ready:
        raise HTTPException(409, "Only a converted version can be used for another output")
    media = db.query(MediaFile).filter(MediaFile.version_id == source.id).all()
    if len(media) != 1 or not media[0].s3_key_processed:
        raise HTTPException(409, "Only a converted version can be used for another output")

    if target_asset_id is not None:
        # Locked: two copies into one asset get consecutive numbers instead of a conflict.
        target = _live_asset(db, target_asset_id, lock=True)
        if target is None:
            raise HTTPException(404, "Asset not found")
        if target.project_id != source_asset.project_id:
            raise HTTPException(400, "The target video belongs to another project")
        if target.id == source_asset.id:
            raise HTTPException(400, "Choose another video than the source")
        if target.asset_type != AssetType.video:
            raise HTTPException(400, "The target is not a video")
    else:
        name = (asset_name or "").strip()
        if not name:
            raise HTTPException(422, "A name for the new video is required")
        target = Asset(
            project_id=source_asset.project_id, name=name[:255], asset_type=AssetType.video, created_by=user.id,
        )
        db.add(target)
        db.flush()

    version = AssetVersion(
        asset_id=target.id,
        version_number=next_version_number(db, target.id),
        processing_status=ProcessingStatus.ready,
        created_by=user.id,
    )
    db.add(version)
    try:
        db.flush()
    except IntegrityError:
        # An upload into the same asset took the number at this moment.
        raise HTTPException(409, "Another version was just added to this video. Try again.") from None
    db.add(MediaFile(version_id=version.id, **{column: getattr(media[0], column) for column in _MEDIA_COPY_COLUMNS}))
    copied = _copy_comments(db, source, target, version) if copy_comments else 0
    log_activity(
        db, "jrc_version_duplicated", user_id=user.id, project_id=target.project_id, asset_id=target.id,
        payload={
            "source_asset_id": str(source_asset.id), "source_version_id": str(source.id),
            "version_id": str(version.id), "comments_copied": copied,
        },
    )
    db.flush()
    return {
        "asset_id": target.id,
        "version_id": version.id,
        "version_number": version.version_number,
        "comments_copied": copied,
    }


def _copy_comments(db: Session, source: AssetVersion, target: Asset, version: AssetVersion) -> int:
    """Copy the visible comment threads of ``source`` to ``version``; returns how many comments.

    Only what the source shows: live comments under live parents (a deleted comment hides its
    replies, as the comment list does). Ids are new, everything else is kept as it was.
    """
    rows = db.query(Comment).filter(
        Comment.version_id == source.id, Comment.deleted_at.is_(None),
    ).order_by(Comment.created_at, Comment.id).all()
    children: dict = {}
    for row in rows:
        children.setdefault(row.parent_id, []).append(row)
    new_ids: dict = {}
    queue = list(children.get(None, []))
    while queue:
        old = queue.pop(0)
        copy = Comment(
            id=uuid.uuid4(),
            asset_id=target.id,
            version_id=version.id,
            parent_id=new_ids.get(old.parent_id),
            author_id=old.author_id,
            guest_author_id=old.guest_author_id,
            timecode_start=old.timecode_start,
            timecode_end=old.timecode_end,
            body=old.body,
            resolved=bool(old.resolved),
            visibility=old.visibility,
            created_at=old.created_at,
            updated_at=old.updated_at,
        )
        db.add(copy)
        new_ids[old.id] = copy.id
        queue.extend(children.get(old.id, []))
    if not new_ids:
        return 0
    db.flush()
    old_ids = list(new_ids)
    for item in db.query(Annotation).filter(Annotation.comment_id.in_(old_ids)).all():
        db.add(Annotation(
            comment_id=new_ids[item.comment_id], drawing_data=item.drawing_data,
            frame_number=item.frame_number, carousel_position=item.carousel_position,
        ))
    for item in db.query(CommentAttachment).filter(CommentAttachment.comment_id.in_(old_ids)).all():
        # Same object, second row: deleting either attachment keeps the file for the other.
        db.add(CommentAttachment(
            comment_id=new_ids[item.comment_id], file_type=item.file_type, s3_key=item.s3_key,
            original_filename=item.original_filename, file_size_bytes=item.file_size_bytes,
            created_at=item.created_at,
        ))
    for item in db.query(CommentReaction).filter(CommentReaction.comment_id.in_(old_ids)).all():
        db.add(CommentReaction(
            comment_id=new_ids[item.comment_id], user_id=item.user_id, emoji=item.emoji, created_at=item.created_at,
        ))
    return len(new_ids)
