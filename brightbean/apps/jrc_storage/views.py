"""Session-only browser files, with live ownership checks and bounded reads (P1-B03)."""

import mimetypes
import posixpath
import re
import time
from functools import lru_cache
from urllib.parse import quote

import requests
from botocore.config import Config
from botocore.exceptions import ClientError, ConnectTimeoutError, ReadTimeoutError
from django.contrib.auth import get_user_model
from django.db.models import Q
from django.http import Http404, HttpResponse, StreamingHttpResponse
from django.views.decorators.http import require_safe

from apps.approvals.comments import can_view_comment
from apps.approvals.models import PostComment
from apps.jrc_video.client import FreeFrameError, FreeFrameUserClient
from apps.jrc_video.models import VideoAccount
from apps.media_library.models import MediaAsset
from apps.members.models import OrgMembership, WorkspaceMembership
from apps.workspaces.models import Workspace

from .limits import TransferUnavailableError, acquire
from .s3 import client_and_bucket

CHUNK_BYTES = 64 * 1024
TIMEOUT = 300


def _authorized(user, key, workspace=None):
    workspaces = WorkspaceMembership.objects.filter(
        user=user, workspace__organization__deleted_at__isnull=True, workspace__is_archived=False
    )
    organizations = OrgMembership.objects.filter(user=user, organization__deleted_at__isnull=True)
    if workspace is not None and not workspaces.filter(workspace=workspace).exists():
        raise Http404
    if key.startswith("comment_attachments/"):
        comment = PostComment.objects.select_related("post").filter(attachment=key, deleted_at__isnull=True).first()
        if not comment or comment.post.workspace.organization.deleted_at or not can_view_comment(comment, user):
            raise Http404
        return
    access = Q(workspace_id__in=workspaces.values("workspace_id")) | Q(
        workspace__isnull=True, organization_id__in=organizations.values("organization_id")
    )
    files = Q(file=key) | Q(thumbnail=key) | Q(versions__file=key) | Q(versions__thumbnail=key)
    if MediaAsset.objects.filter(access).filter(files).exists():
        return
    if Workspace.objects.filter(pk__in=workspaces.values("workspace_id"), icon=key).exists():
        return
    owner = get_user_model().objects.filter(avatar=key, is_active=True).first()
    if owner and (
        owner.pk == user.pk
        or WorkspaceMembership.objects.filter(user=owner, workspace_id__in=workspaces.values("workspace_id")).exists()
    ):
        return
    raise Http404


def _headers(response):
    response["Cache-Control"] = "private, no-store"
    response["X-Content-Type-Options"] = "nosniff"
    response["Content-Security-Policy"] = "sandbox; default-src 'none'"
    response["Accept-Ranges"] = "bytes"
    return response


class FileStream(StreamingHttpResponse):
    """Django closes responses even if the iterator was never started."""

    def __init__(self, iterator, *, close, release=lambda: None, **kwargs):
        super().__init__(iterator, **kwargs)
        self._resource_closers.extend([close, release])


def _stream(read, close, release, *, status=200, content_type=None, headers=None):
    deadline = time.monotonic() + TIMEOUT

    def chunks():
        try:
            while True:
                if time.monotonic() > deadline:
                    raise RuntimeError("File transfer deadline exceeded")
                chunk = read(CHUNK_BYTES)
                if not chunk:
                    break
                yield chunk
        finally:
            close()
            release()

    return _headers(
        FileStream(chunks(), close=close, release=release, status=status, content_type=content_type, headers=headers)
    )


def _range(value, size):
    if not value:
        return None
    match = re.fullmatch(r"bytes=(\d*)-(\d*)", value)
    if not match or not any(match.groups()):
        raise ValueError
    first, last = match.groups()
    start = int(first) if first else max(0, size - int(last))
    end = min(int(last), size - 1) if first and last else size - 1
    if start > end or start >= size or not size:
        raise ValueError
    return start, end


@lru_cache(maxsize=1)
def _storage():
    from django.core.files.storage import default_storage

    client, bucket = client_and_bucket()
    return default_storage._create_session().client(
        "s3",
        endpoint_url=client.meta.endpoint_url,
        region_name="garage",
        config=Config(
            signature_version="s3v4",
            s3={"addressing_style": "path"},
            connect_timeout=3,
            read_timeout=5,
            retries={"total_max_attempts": 1},
        ),
    ), bucket


def stored_file(request, key, *, workspace=None):
    """Every entry point checks current access before reserving or reading bytes."""
    if not request.user.is_authenticated or not request.user.is_active:
        return _headers(HttpResponse(status=403))
    _authorized(request.user, key, workspace)
    try:
        release = acquire(request.user)
    except TransferUnavailableError as exc:
        return _headers(HttpResponse(status=exc.status, headers={"Retry-After": "60"}))
    try:
        client, bucket = _storage()
        size = client.head_object(Bucket=bucket, Key=key)["ContentLength"]
        try:
            window = _range(request.headers.get("Range"), size)
        except ValueError:
            release()
            return _headers(HttpResponse(status=416, headers={"Content-Range": f"bytes */{size}"}))
        params = {"Bucket": bucket, "Key": key}
        headers = {"Content-Length": str(size)}
        if window:
            start, end = window
            params["Range"] = f"bytes={start}-{end}"
            headers.update({"Content-Length": str(end - start + 1), "Content-Range": f"bytes {start}-{end}/{size}"})
        content_type = mimetypes.guess_type(key)[0] or "application/octet-stream"
        if request.method == "HEAD":
            release()
            return _headers(HttpResponse(status=206 if window else 200, content_type=content_type, headers=headers))
        body = client.get_object(**params)["Body"]
        return _stream(
            body.read,
            body.close,
            release,
            status=206 if window else 200,
            content_type=content_type,
            headers=headers,
        )
    except (ConnectTimeoutError, ReadTimeoutError):
        release()
        return _headers(HttpResponse(status=504))
    except ClientError as exc:
        release()
        if str(exc.response.get("Error", {}).get("Code")) in ("404", "NoSuchKey", "NotFound"):
            raise Http404 from None
        return _headers(HttpResponse(status=503))
    except Exception:
        release()
        raise


def _video_file(request, key):
    if not VideoAccount.objects.filter(user=request.user).exists():
        raise Http404
    api = FreeFrameUserClient(request.user)
    upstream = None
    try:
        for renew in (False, True):
            api._authenticate(renew=renew)
            headers = {"Authorization": f"Bearer {api.token}"}
            if request.headers.get("Range"):
                headers["Range"] = request.headers["Range"]
            # Fixed internal host, exact key; cookies never leave the application.
            upstream = requests.request(
                request.method,
                api.base_url + "/files/" + quote(key, safe="/"),
                params={k: request.GET[k] for k in ("filename", "share") if k in request.GET},
                headers=headers,
                stream=True,
                timeout=(3, 5),
                allow_redirects=False,
                proxies={"http": "", "https": ""},
            )
            if upstream.status_code != 401 or renew:
                break
            upstream.close()
        passed = {
            k: v
            for k, v in upstream.headers.items()
            if k.lower()
            in (
                "content-length",
                "content-range",
                "content-disposition",
                "retry-after",
            )
        }
        if upstream.status_code not in (200, 206):
            status = upstream.status_code if upstream.status_code in (401, 403, 404, 410, 416, 429, 503, 504) else 502
            upstream.close()
            # The error body is deliberately empty. Its length must not claim
            # the upstream JSON body's bytes or the proxy will reset the socket.
            error_headers = {k: v for k, v in passed.items() if k.lower() in ("retry-after", "content-range")}
            return _headers(HttpResponse(status=status, headers=error_headers))
        if request.method == "HEAD":
            upstream.close()
            return _headers(
                HttpResponse(
                    status=upstream.status_code, headers=passed, content_type=upstream.headers.get("Content-Type")
                )
            )
        return _stream(
            upstream.raw.read,
            upstream.close,
            lambda: None,
            status=upstream.status_code,
            headers=passed,
            content_type=upstream.headers.get("Content-Type"),
        )
    except (FreeFrameError, requests.RequestException):
        if upstream is not None:
            upstream.close()
        return _headers(HttpResponse(status=503))


@require_safe
def private_file(request, path):
    if not request.user.is_authenticated or not request.user.is_active:
        return _headers(HttpResponse(status=403))
    if "\\" in path or posixpath.normpath(path) != path or path.startswith("/"):
        raise Http404
    if path.startswith("video/"):
        return _video_file(request, path.removeprefix("video/"))
    return stored_file(request, path)
