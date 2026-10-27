"""Private object bytes for the session gateway; never redirects to storage (P1-B03)."""
import json
import posixpath
import uuid
from time import monotonic

from botocore.exceptions import ClientError, ConnectTimeoutError, ReadTimeoutError
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from sqlalchemy import or_
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db
from ..middleware.auth import get_current_user
from ..models.asset import Asset, AssetVersion, MediaFile, ProcessingStatus
from ..models.branding import ProjectBranding
from ..models.comment import Comment, CommentAttachment
from ..models.instance_branding import InstanceBranding
from ..models.project import Project, ProjectMember
from ..models.share import ShareLink
from ..models.user import User
from ..services.comment_privacy import hides_internal_comments, thread_is_internal
from ..services.hls_limits import hls_limits
from ..services.permissions import get_project_member, validate_share_link_with_session, validate_asset_in_share
from ..services.redis_service import get_redis
from ..services.s3_service import build_content_disposition
from .hls_proxy import _HlsStreamResponse, _stream_s3_client, _share_security_fingerprint

router = APIRouter(tags=["files"])
HEADERS = {"Cache-Control": "private, no-store", "Accept-Ranges": "bytes",
           "X-Content-Type-Options": "nosniff", "Content-Security-Policy": "sandbox; default-src 'none'"}


def _member(db, project_id, user):
    if not get_project_member(db, project_id, user.id):
        raise HTTPException(404, "File not found")


def authorize_file(db, user, key):
    """Resolve an exact stored key, never a directory grant. Unknown uploads deny."""
    attachment = db.query(CommentAttachment).filter(CommentAttachment.s3_key == key).first()
    if attachment:
        comment = db.query(Comment).filter(Comment.id == attachment.comment_id, Comment.deleted_at.is_(None)).first()
        if not comment:
            raise HTTPException(404, "File not found")
        asset = db.query(Asset).filter(Asset.id == comment.asset_id, Asset.deleted_at.is_(None)).first()
        if not asset:
            raise HTTPException(404, "File not found")
        _member(db, asset.project_id, user)
        if hides_internal_comments(db, asset.project_id, user) and thread_is_internal(db, comment):
            raise HTTPException(404, "File not found")
        return asset, None, attachment.original_filename, comment.version_id

    row = db.query(MediaFile, Asset).join(AssetVersion, AssetVersion.id == MediaFile.version_id).join(
        Asset, Asset.id == AssetVersion.asset_id,
    ).filter(
        or_(MediaFile.s3_key_raw == key, MediaFile.s3_key_processed == key,
            MediaFile.s3_key_download == key, MediaFile.s3_key_thumbnail == key),
        AssetVersion.deleted_at.is_(None), Asset.deleted_at.is_(None),
        AssetVersion.processing_status == ProcessingStatus.ready,
    ).first()
    if row:
        media, asset = row
        _member(db, asset.project_id, user)
        return asset, media, None, media.version_id

    project = db.query(Project).filter(Project.poster_s3_key == key, Project.deleted_at.is_(None)).first()
    if project:
        _member(db, project.id, user)
        return None, None, None, None
    branding = db.query(ProjectBranding).filter(ProjectBranding.logo_s3_key == key).first()
    if branding:
        _member(db, branding.project_id, user)
        return None, None, None, None
    owner = db.query(User).filter(User.avatar_url == key, User.deleted_at.is_(None)).first()
    if owner:
        shared = db.query(ProjectMember.project_id).filter(
            ProjectMember.user_id == user.id, ProjectMember.deleted_at.is_(None),
        ).subquery()
        peer = db.query(ProjectMember).join(Project, Project.id == ProjectMember.project_id).filter(
            ProjectMember.user_id == owner.id, ProjectMember.deleted_at.is_(None),
            ProjectMember.project_id.in_(db.query(shared.c.project_id)), Project.deleted_at.is_(None),
        ).first()
        if owner.id == user.id or peer:
            return None, None, None, None
    branding = db.query(InstanceBranding).filter(or_(
        InstanceBranding.logo_light_key == key, InstanceBranding.logo_dark_key == key,
        InstanceBranding.favicon_key == key, InstanceBranding.apple_icon_key == key,
        InstanceBranding.login_logo_key == key,
    )).first()
    if branding:
        return None, None, None, None
    raise HTTPException(404, "File not found")


def authorize_share_file(db, user, binding, asset, version_id):
    from .share import _share_media_file

    link = db.query(ShareLink).filter(ShareLink.id == uuid.UUID(binding["share_id"])).first()
    if not link or not asset:
        raise HTTPException(404, "File not found")
    if binding["security"] != _share_security_fingerprint(link):
        raise HTTPException(403, "Reopen the share link")
    link = validate_share_link_with_session(db, link.token, share_session=binding["session"], current_user=user)
    validate_asset_in_share(db, link, asset)
    if binding["operation"] == "download" and not link.allow_download:
        raise HTTPException(403, "Downloads are disabled")
    if version_id:
        selected = _share_media_file(db, link, asset, version_id)
        if selected.version_id != version_id:
            raise HTTPException(404, "Version is no longer shared")


def _file_context(user, context_id):
    try:
        context_id = uuid.UUID(context_id)
    except ValueError:
        raise HTTPException(404, "File not found") from None
    try:
        stored = get_redis().get(f"files:context:{user.id}:{context_id}")
        binding = json.loads(stored) if stored else None
    except Exception:
        raise HTTPException(503, "File service unavailable") from None
    if not binding:
        raise HTTPException(404, "File context not found; reopen the share link")
    return binding


@router.get("/files/{key:path}")
@router.head("/files/{key:path}", include_in_schema=False)
def file_bytes(
    key: str, request: Request, filename: str | None = Query(None, max_length=255),
    share: uuid.UUID | None = None, db: Session = Depends(get_db), user: User = Depends(get_current_user),
):
    if "\\" in key or posixpath.normpath(key) != key or key.startswith("/"):
        raise HTTPException(404, "File not found")
    binding = None
    if key.startswith("_contexts/"):
        binding = _file_context(user, key.removeprefix("_contexts/"))
        key = binding["key"]
        filename = binding["filename"]
    # Old optional-share addresses cannot fall back to ordinary member access.
    if share:
        raise HTTPException(404, "Reopen the share link")
    asset, media, attachment_name, version_id = authorize_file(db, user, key)
    if binding:
        authorize_share_file(db, user, binding, asset, version_id)
    release = hls_limits.acquire(f"user:{user.id}")
    try:
        s3 = _stream_s3_client()
        params = {"Bucket": settings.s3_bucket, "Key": key}
        if request.headers.get("range"):
            import re
            if not re.fullmatch(r"bytes=(\d+-\d*|-\d+)", request.headers["range"]):
                raise HTTPException(416, "Invalid range")
            params["Range"] = request.headers["range"]
        obj = s3.get_object(**params)
        body = obj["Body"]
        headers = dict(HEADERS, **{"Content-Length": str(obj["ContentLength"])})
        if obj.get("ContentRange"):
            headers["Content-Range"] = obj["ContentRange"]
        name = attachment_name or filename
        if name:
            headers["Content-Disposition"] = build_content_disposition(name)
        status = 206 if obj.get("ContentRange") else 200
        if request.method == "HEAD":
            body.close()
            release()
            return Response(status_code=status, headers=headers, media_type=obj.get("ContentType"))
        deadline = monotonic() + settings.file_stream_timeout_seconds

        def chunks():
            try:
                for chunk in body.iter_chunks(chunk_size=64 * 1024):
                    if monotonic() > deadline:
                        raise RuntimeError("File transfer deadline exceeded")
                    yield chunk
            finally:
                body.close()
                release()
        response = _HlsStreamResponse(chunks(), body=body, headers=headers, status_code=status, media_type=obj.get("ContentType"))
        response.timeout_seconds = settings.file_stream_timeout_seconds
        response.release = release
        return response
    except ClientError as exc:
        release()
        code = str(exc.response.get("Error", {}).get("Code"))
        raise HTTPException(416 if code == "InvalidRange" else 404, "File not found or invalid range", headers=HEADERS) from None
    except (ConnectTimeoutError, ReadTimeoutError):
        release()
        raise HTTPException(504, "Storage timed out", headers=HEADERS) from None
    except BaseException:
        release()
        raise
