"""HLS proxy for secure video streaming.

Rewrites m3u8 manifests so that:
- Variant playlist URLs go through this proxy (with token auth)
- Managed segment requests also check live membership before a short S3 redirect
- Other segment (.ts) URLs become presigned S3 URLs (direct to S3)

This eliminates the need for a public bucket policy on processed/*.
"""

import logging
import posixpath
import uuid
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response, RedirectResponse
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db
from ..models.user import UserStatus
from ..models.project import Project, ProjectRole
from ..services.auth_service import get_user_by_id
from ..services.permissions import require_project_role
from ..services.s3_service import generate_presigned_get_url, get_s3_client

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/stream", tags=["streaming"])


MANAGED_MEDIA_SECONDS = 180
# HLS credentials are purpose-bound, unlike the long-lived login JWT. A player
# asks for a fresh stream URL when the playlist session expires.
HLS_TOKEN_SECONDS = 30 * 60


def managed_hls_identity(user, project_id, *, db: Session | None = None) -> dict:
    if user is None:
        return {}
    managed_project = db is not None and db.query(Project.jrc_managed).filter(Project.id == project_id).scalar() is True
    if user.jrc_managed is not True and not managed_project:
        return {}
    return {
        "managed": True, "user_id": str(user.id), "project_id": str(project_id),
        "token_version": user.token_version,
    }


def create_hls_token(
    s3_prefix: str, expires_seconds: int = HLS_TOKEN_SECONDS, *, managed: bool = False,
    user_id: str | None = None, project_id: str | None = None, token_version: int | None = None,
) -> str:
    """Create an HLS token; managed sessions also carry a live identity binding."""
    payload = {
        "sub": "hls",
        # v4 invalidates previously issued 24-hour HLS URLs at deployment.
        "v": 4,
        "pfx": s3_prefix,
        "exp": datetime.now(timezone.utc) + timedelta(seconds=expires_seconds),
    }
    if managed:
        if not user_id or not project_id or token_version is None:
            raise ValueError("Managed HLS requires a user, project and token version")
        payload.update(uid=user_id, pid=project_id, ver=token_version)
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


# The longest a segment URL is signed for: the lifetime create_hls_token gives a token.
SEGMENT_URL_MAX_SECONDS = HLS_TOKEN_SECONDS


def _decode_hls_token(token: str) -> dict:
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    if payload.get("sub") != "hls":
        raise HTTPException(status_code=403, detail="Invalid token type")
    # Earlier formats used 24-hour URLs. Invalidate them rather than leaving old
    # credentials valid after the logging and lifetime protections are deployed.
    if payload.get("v") != 4:
        raise HTTPException(status_code=401, detail="Reload the video to renew its streaming session")
    return payload


def _verify_hls_token(token: str) -> str:
    """Verify HLS token and return s3_prefix."""
    return _decode_hls_token(token)["pfx"]


def _segment_url_lifetime(payload: dict) -> int:
    """Seconds a segment URL signed now may live: what is left of the token, never
    more. The proxy checks only the token, so a segment URL that outlived it would
    keep video playing past the token's expiry -- after access to the asset ended."""
    exp = payload.get("exp")
    if exp is None:
        return SEGMENT_URL_MAX_SECONDS
    remaining = int(exp - datetime.now(timezone.utc).timestamp())
    maximum = MANAGED_MEDIA_SECONDS if "uid" in payload else SEGMENT_URL_MAX_SECONDS
    return max(1, min(maximum, remaining))


def _rewrite_manifest(
    content: str,
    s3_prefix: str,
    manifest_path: str,
    token: str,
    segment_expires_in: int = SEGMENT_URL_MAX_SECONDS,
    proxy_segments: bool = False,
) -> str:
    """Rewrite URLs in an m3u8 manifest.

    - .m3u8 references -> proxy URLs with token (appended as query param)
    - .ts references -> presigned S3 URLs
    """
    manifest_dir = posixpath.dirname(manifest_path)
    lines = content.split("\n")
    result = []

    for line in lines:
        stripped = line.strip()

        # Pass through comments/tags and empty lines
        if not stripped or stripped.startswith("#"):
            result.append(line)
            continue

        # Resolve segment/playlist path relative to current manifest directory
        if manifest_dir:
            relative_key = f"{manifest_dir}/{stripped}"
        else:
            relative_key = stripped

        if stripped.endswith(".m3u8"):
            # Variant playlist -> proxy URL with token
            result.append(f"{relative_key}?token={token}")
        elif stripped.endswith(".ts"):
            if proxy_segments:
                # Keep the request at the proxy so membership is checked for
                # every segment, including long videos and seeking after pause.
                result.append(f"{stripped}?token={token}")
                continue
            # Segment -> presigned S3 URL (direct to S3), signed for what is
            # left of the token, so pause-and-resume works for as long as the
            # token does and no longer
            s3_key = f"{s3_prefix}/{relative_key}"
            result.append(generate_presigned_get_url(s3_key, expires_in=segment_expires_in))
        else:
            result.append(line)

    return "\n".join(result)


def _authorize_managed_hls(payload: dict, db: Session):
    if "uid" not in payload:
        return
    try:
        user = get_user_by_id(db, uuid.UUID(payload["uid"]))
        project_id = uuid.UUID(payload["pid"])
    except (KeyError, ValueError, TypeError):
        raise HTTPException(status_code=401, detail="Invalid streaming session") from None
    if not user or user.status != UserStatus.active or user.token_version != payload.get("ver"):
        raise HTTPException(status_code=401, detail="Streaming session revoked")
    require_project_role(db, project_id, user, ProjectRole.viewer)


@router.get("/hls/{path:path}")
def hls_proxy(path: str, token: str = Query(...), db: Session = Depends(get_db)):
    """Proxy HLS manifests with URL rewriting for secure streaming."""
    payload = _decode_hls_token(token)
    _authorize_managed_hls(payload, db)
    s3_prefix = payload["pfx"]

    # Managed segments check membership before redirecting to private S3.
    managed_segment = "uid" in payload and path.endswith(".ts")
    if not path.endswith(".m3u8") and not managed_segment:
        raise HTTPException(status_code=400, detail="Only .m3u8 files are proxied")

    # Prevent directory traversal
    normalised = posixpath.normpath(path)
    if normalised.startswith("..") or normalised.startswith("/"):
        raise HTTPException(status_code=400, detail="Invalid path")

    # Defense-in-depth: verify resolved key stays within the token's prefix
    s3_key = f"{s3_prefix}/{normalised}"
    if not s3_key.startswith(s3_prefix + "/"):
        raise HTTPException(status_code=400, detail="Invalid path")

    if managed_segment:
        return RedirectResponse(
            generate_presigned_get_url(s3_key, expires_in=_segment_url_lifetime(payload)),
            status_code=302, headers={"Cache-Control": "no-store"},
        )

    # Fetch manifest from S3
    s3 = get_s3_client()
    try:
        obj = s3.get_object(Bucket=settings.s3_bucket, Key=s3_key)
        content = obj["Body"].read().decode("utf-8")
    except s3.exceptions.NoSuchKey:
        raise HTTPException(status_code=404, detail="Manifest not found")
    except Exception as e:
        logger.error("Failed to fetch HLS manifest %s: %s", s3_key, e)
        raise HTTPException(status_code=404, detail="Manifest not found")

    rewritten = _rewrite_manifest(
        content, s3_prefix, normalised, token, _segment_url_lifetime(payload),
        proxy_segments="uid" in payload,
    )

    return Response(
        content=rewritten,
        media_type="application/vnd.apple.mpegurl",
        headers={"Cache-Control": "no-store"},
    )
