"""HLS proxy for secure video streaming.

Rewrites m3u8 manifests so that:
- Variant playlist URLs go through this proxy (with scoped HttpOnly cookies)
- Managed and shared segments check live access and stream from private S3
- Other segment (.ts) URLs become presigned S3 URLs (direct to S3)

This eliminates the need for a public bucket policy on processed/*.
"""

import hashlib
import logging
import posixpath
import uuid
from functools import lru_cache
from time import monotonic
from datetime import datetime, timedelta, timezone

import jwt
import anyio
from botocore.config import Config
from botocore.exceptions import ConnectTimeoutError, ReadTimeoutError
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response, StreamingResponse
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db
from ..models.user import UserStatus
from ..models.project import Project, ProjectRole
from ..models.share import ShareLink
from ..services.hls_limits import hls_limits
from ..services.auth_service import get_user_by_id
from ..services.permissions import require_project_role
from ..services.s3_service import generate_presigned_get_url, get_s3_client, _build_s3_client

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
    share: dict | None = None,
) -> str:
    """Create an HLS token; managed sessions also carry a live identity binding."""
    payload = {
        "sub": "hls",
        # v5 also invalidates share cookies issued without a live link binding.
        "v": 5,
        "pfx": s3_prefix,
        "exp": datetime.now(timezone.utc) + timedelta(seconds=expires_seconds),
    }
    if managed:
        if not user_id or not project_id or token_version is None:
            raise ValueError("Managed HLS requires a user, project and token version")
        payload.update(uid=user_id, pid=project_id, ver=token_version)
    if share is not None:
        payload["share"] = share
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


# The longest a segment URL is signed for: the lifetime create_hls_token gives a token.
SEGMENT_URL_MAX_SECONDS = HLS_TOKEN_SECONDS


HLS_COOKIE = "ff_hls"
SEGMENT_CHUNK_BYTES = 64 * 1024


def hls_session_id(s3_prefix: str) -> str:
    # A public identifier, not a credential. Separate cookie paths allow two
    # versions to play side by side without replacing each other's session.
    return hashlib.sha256(s3_prefix.encode()).hexdigest()[:32]


def issue_hls_url(request: Request, response: Response, s3_prefix: str, **identity) -> str:
    path = f"/stream/hls/{hls_session_id(s3_prefix)}/"
    response.set_cookie(
        HLS_COOKIE, create_hls_token(s3_prefix, **identity),
        max_age=HLS_TOKEN_SECONDS,
        path=request.scope.get("root_path", "").rstrip("/") + path,
        httponly=True, samesite="strict", secure=request.url.scheme == "https",
    )
    response.headers["Cache-Control"] = "no-store"
    return path + "master.m3u8"


def _decode_hls_token(token: str) -> dict:
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    if payload.get("sub") != "hls":
        raise HTTPException(status_code=403, detail="Invalid token type")
    # Earlier formats used long-lived URLs or omitted the share-link binding.
    if payload.get("v") != 5:
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
    segment_expires_in: int = SEGMENT_URL_MAX_SECONDS,
    proxy_segments: bool = False,
) -> str:
    """Rewrite URLs in an m3u8 manifest.

    - .m3u8 references -> relative proxy URLs (credential in an HttpOnly cookie)
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
            # Resolve relative to the current playlist, within its cookie path.
            result.append(stripped)
        elif stripped.endswith(".ts"):
            if proxy_segments:
                # Keep the request at the proxy so membership is checked for
                # every segment, including long videos and seeking after pause.
                result.append(stripped)
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


def _share_security_fingerprint(link) -> str:
    values = (link.token, link.password_hash, link.visibility, link.permission)
    return hashlib.sha256("\0".join(str(value or "") for value in values).encode()).hexdigest()


def share_hls_identity(link, asset, media_file, current_user, share_session=None) -> dict:
    """Keep link credentials inside the scoped HttpOnly cookie, never in a URL."""
    return {"share": {
        "id": str(link.id), "asset": str(asset.id),
        "version": str(media_file.version_id) if media_file.version_id else None,
        "user": str(current_user.id) if current_user else None,
        "ver": current_user.token_version if current_user else None,
        "session": share_session,
        "security": _share_security_fingerprint(link),
    }}


def _authorize_share_hls(payload: dict, db: Session):
    binding = payload.get("share")
    if binding is None:
        return
    # Imported at call time: share endpoints issue these HLS cookies themselves.
    from .share import _get_asset, _share_media_file
    from ..services.permissions import validate_share_link_with_session, validate_asset_in_share

    link = db.query(ShareLink).filter(ShareLink.id == uuid.UUID(binding["id"])).first()
    if not link:
        raise HTTPException(404, "Share link not found")
    user = get_user_by_id(db, uuid.UUID(binding["user"])) if binding["user"] else None
    if binding["user"] and (not user or user.status != UserStatus.active or user.token_version != binding["ver"]):
        raise HTTPException(401, "Streaming session revoked")
    link = validate_share_link_with_session(db, link.token, share_session=binding["session"], current_user=user)
    if _share_security_fingerprint(link) != binding["security"]:
        raise HTTPException(403, "Share permissions changed; reopen the link")
    asset = _get_asset(db, uuid.UUID(binding["asset"]))
    validate_asset_in_share(db, link, asset)
    version = uuid.UUID(binding["version"]) if binding["version"] else None
    media = _share_media_file(db, link, asset, version)
    if media.s3_key_processed != payload["pfx"]:
        raise HTTPException(403, "Video is no longer shared")


@lru_cache(maxsize=1)
def _stream_s3_client():
    return _build_s3_client(Config(
        connect_timeout=settings.hls_s3_connect_timeout_seconds,
        read_timeout=settings.hls_s3_read_timeout_seconds,
        retries={"total_max_attempts": 1},
    ))


class _HlsStreamResponse(StreamingResponse):
    """Close storage and release capacity even on cancellation or iterator failure."""
    release = None

    def __init__(self, *args, body, **kwargs):
        super().__init__(*args, **kwargs)
        self.storage_body = body

    async def __call__(self, scope, receive, send):
        try:
            with anyio.fail_after(settings.hls_stream_timeout_seconds):
                await super().__call__(scope, receive, send)
        finally:
            try:
                self.storage_body.close()
            finally:
                if self.release:
                    self.release()


@router.get("/hls/{session_id}/{path:path}")
def browser_hls_proxy(session_id: str, path: str, request: Request, db: Session = Depends(get_db)):
    token = request.cookies.get(HLS_COOKIE)
    if not token:
        raise HTTPException(status_code=401, detail="Missing streaming session")
    payload = _decode_hls_token(token)
    if session_id != hls_session_id(payload["pfx"]):
        raise HTTPException(status_code=403, detail="Invalid streaming session")
    ip = request.headers.get("x-real-ip") or (request.client.host if request.client else "unknown")
    release = hls_limits.acquire(ip, hashlib.sha256(token.encode()).hexdigest())
    try:
        response = hls_proxy(path, token, db)
        if isinstance(response, _HlsStreamResponse):
            response.release = release
            return response
    except BaseException:
        release()
        raise
    release()
    return response


def hls_proxy(path: str, token: str, db: Session = Depends(get_db)):
    """Proxy HLS manifests with URL rewriting for secure streaming."""
    payload = _decode_hls_token(token)
    _authorize_managed_hls(payload, db)
    _authorize_share_hls(payload, db)
    s3_prefix = payload["pfx"]

    # Managed and share segments recheck access before reading private S3.
    proxy_segments = "uid" in payload or "share" in payload
    managed_segment = proxy_segments and path.endswith(".ts")
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
        s3 = _stream_s3_client()
        deadline = monotonic() + settings.hls_stream_timeout_seconds
        try:
            obj = s3.get_object(Bucket=settings.s3_bucket, Key=s3_key)
        except (ConnectTimeoutError, ReadTimeoutError):
            raise HTTPException(status_code=504, detail="Storage timed out") from None
        except Exception:
            # Do not expose storage details or signed URLs in errors/logs.
            raise HTTPException(status_code=404, detail="Segment not found") from None
        body = obj["Body"]
        if obj.get("ContentLength", 0) > settings.hls_max_segment_bytes:
            body.close()
            raise HTTPException(413, "Segment too large")

        def chunks():
            try:
                size = 0
                for chunk in body.iter_chunks(chunk_size=SEGMENT_CHUNK_BYTES):
                    size += len(chunk)
                    if size > settings.hls_max_segment_bytes or monotonic() > deadline:
                        # Headers may already be sent: terminate without sending
                        # the overflowing chunk or leaking a storage exception.
                        raise RuntimeError("Streaming budget exceeded")
                    yield chunk
            except (ReadTimeoutError, ConnectTimeoutError):
                raise RuntimeError("Storage timed out") from None
            finally:
                body.close()

        return _HlsStreamResponse(
            chunks(), media_type="video/mp2t", headers={"Cache-Control": "no-store"},
            body=body,
        )

    # Fetch manifest from S3
    s3 = _stream_s3_client() if proxy_segments else get_s3_client()
    try:
        obj = s3.get_object(Bucket=settings.s3_bucket, Key=s3_key)
        try:
            content = obj["Body"].read(1024 * 1024 + 1)
            if len(content) > 1024 * 1024:
                raise HTTPException(413, "Manifest too large")
            content = content.decode("utf-8")
        finally:
            obj["Body"].close()
    except HTTPException:
        raise
    except (ConnectTimeoutError, ReadTimeoutError):
        raise HTTPException(504, "Storage timed out") from None
    except s3.exceptions.NoSuchKey:
        raise HTTPException(status_code=404, detail="Manifest not found")
    except Exception as e:
        logger.error("Failed to fetch HLS manifest %s: %s", s3_key, e)
        raise HTTPException(status_code=404, detail="Manifest not found")

    rewritten = _rewrite_manifest(
        content, s3_prefix, normalised, _segment_url_lifetime(payload),
        proxy_segments=proxy_segments,
    )

    return Response(
        content=rewritten,
        media_type="application/vnd.apple.mpegurl",
        headers={"Cache-Control": "no-store"},
    )
