"""Upload of a new channel video version through FreeFrame, on behalf of the user (F5-B05).

The browser sends the file in parts straight to the object storage (Garage) using short-lived
signed addresses. This server only brokers the three FreeFrame calls (initiate, presign a part,
complete) with the *requesting user's* own short token (``FreeFrameUserClient``), so FreeFrame
records the real author and enforces that user's project role. No FreeFrame token ever reaches
a browser from here.
"""

import logging
import os
import uuid
from urllib.parse import parse_qs, urlsplit

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.composer.models import PlatformPost
from apps.jrc_production.notifications import notify_video_version
from apps.jrc_texts import services as text_services
from apps.members.models import WorkspaceMembership

from .client import FreeFrameClient, FreeFrameError, FreeFrameUserClient
from .models import VideoAccount, VideoAsset, VideoProject, VideoVersion
from .services import video_role

logger = logging.getLogger(__name__)

# Extension decides the type sent to FreeFrame; the type the browser reports is not trusted.
ALLOWED_EXTENSIONS = {
    ".mp4": "video/mp4",
    ".m4v": "video/x-m4v",
    ".mov": "video/quicktime",
    ".webm": "video/webm",
    ".mkv": "video/x-matroska",
}
DEFAULT_MAX_BYTES = 4 * 1024**3
# A signed part address must not live longer than this (FreeFrame signs for 900 s).
MAX_PART_URL_SECONDS = 900
MAX_FILENAME_LENGTH = 200
MAX_PART_NUMBER = 10000
UNFINISHED = (VideoVersion.Status.UPLOADING, VideoVersion.Status.PROCESSING)


class UploadError(Exception):
    """A problem shown to the person uploading. ``status`` is the HTTP status for the view."""

    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def max_upload_bytes():
    return int(getattr(settings, "JRC_VIDEO_MAX_UPLOAD_BYTES", DEFAULT_MAX_BYTES))


def allowed_extensions_attr():
    return ",".join(sorted(ALLOWED_EXTENSIONS))


def can_upload(post, membership):
    """Server-side rule for "Upload version": a FreeFrame editor who may edit this content."""
    return bool(
        membership is not None
        and not post.workspace.is_archived
        and video_role(membership) == "editor"
        and text_services.can_edit_text(post, membership)
    )


def clean_filename(name):
    name = os.path.basename(str(name or "").replace("\\", "/"))
    name = "".join(c for c in name if c.isprintable() and c not in '<>:"|?*').strip(" .")
    if not name or len(name) > MAX_FILENAME_LENGTH:
        raise UploadError("Choose a video file with a valid name.")
    extension = os.path.splitext(name)[1].lower()
    if extension not in ALLOWED_EXTENSIONS:
        raise UploadError("Unsupported file type. Use " + ", ".join(sorted(ALLOWED_EXTENSIONS)) + ".")
    return name, ALLOWED_EXTENSIONS[extension]


def _user_message(exc):
    code = getattr(exc, "status_code", None)
    if code in (401, 403):
        return "You are not allowed to upload video for this client.", 403
    if code == 404:
        return "This video no longer exists in the video service.", 404
    if code == 409:
        return "This upload cannot be continued.", 409
    if code == 400:
        return "The video service refused this file.", 400
    return "The video service is unavailable. Try again in a minute.", 503


def _call(user, method, path, payload=None):
    try:
        return FreeFrameUserClient(user).request(method, path, payload)
    except FreeFrameError as exc:
        logger.warning("FreeFrame %s %s for user %s failed: %s", method, path.split("?")[0], user.pk, exc)
        message, status = _user_message(exc)
        raise UploadError(message, status) from None


def _uuid(value):
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        raise UploadError("The video service returned an unexpected answer.", 502) from None


def _int(value, minimum=0):
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise UploadError("The video service returned an unexpected answer.", 502)
    return value


def _label(version):
    return VideoVersion.Status(version.status).label


def _accounts_ready(user, workspace):
    project = VideoProject.objects.filter(workspace=workspace).first()
    if project is None or not VideoAccount.objects.filter(user=user).exists():
        raise UploadError("Video is not ready for this client yet. An administrator can run the synchronization.", 503)
    return project


def _resolve_platform_post(post, key):
    """The channel copy for ``key`` (an existing copy or ``new:<account id>``), created like captions."""
    from django.core.exceptions import ValidationError
    from django.http import Http404

    from apps.composer.models import Post
    from apps.jrc_production.views import _create_missing_copy

    key = str(key or "")
    if key.startswith("new:"):
        try:
            with transaction.atomic():
                # Same lock order as saving a caption: the post row first.
                Post.objects.select_for_update().get(pk=post.pk)
                return _create_missing_copy(post, key.removeprefix("new:"))
        except Http404:
            raise UploadError("This channel is not enabled for this client.", 404) from None
        except ValidationError as exc:
            raise UploadError(" ".join(exc.messages), 409) from None
    try:
        variant = PlatformPost.objects.select_related("social_account").get(pk=uuid.UUID(key), post=post)
    except (ValueError, PlatformPost.DoesNotExist):
        raise UploadError("Choose a channel of this content.", 404) from None
    if not _channel_enabled(post, variant):
        raise UploadError("This channel is not enabled for this client.", 404)
    return variant


def _channel_enabled(post, variant):
    from apps.jrc_production.views import _enabled_caption_accounts

    return any(account.pk == variant.social_account_id for account in _enabled_caption_accounts(post))


def start_upload(user, workspace, post, membership, *, channel, filename, size, browser_type=""):
    """Step 1: create (or reuse) the channel video in FreeFrame and a new unfinished version."""
    if not can_upload(post, membership):
        raise UploadError("You cannot upload video for this content.", 403)
    name, mime = clean_filename(filename)
    if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
        raise UploadError("The file is empty.")
    if size > max_upload_bytes():
        raise UploadError(f"The file is larger than the {max_upload_bytes() // 1024**3} GB limit.")
    if browser_type and not str(browser_type).lower().startswith("video/"):
        raise UploadError("Choose a video file.")
    project = _accounts_ready(user, workspace)
    variant = _resolve_platform_post(post, channel)
    asset_name = f"{post.title or 'Content'} · {variant.social_account.account_name}"[:200]

    with transaction.atomic():
        # One upload start per channel at a time; held across the FreeFrame calls on purpose.
        locked = PlatformPost.objects.select_for_update().get(pk=variant.pk)
        if VideoVersion.objects.filter(
            video__platform_post=locked, uploaded_by=user, status=VideoVersion.Status.UPLOADING
        ).exists():
            raise UploadError("You already have an unfinished upload for this channel. Continue or discard it.", 409)
        video = VideoAsset.objects.filter(platform_post=locked).first()
        body = {
            "project_id": str(project.ff_project_id),
            "asset_name": asset_name,
            "original_filename": name,
            "mime_type": mime,
            "file_size_bytes": size,
        }
        if video is None:
            started = _call(user, "POST", "/upload/initiate", body)
        else:
            started = _call(user, "POST", f"/assets/{video.ff_asset_id}/versions", body)
        if not isinstance(started, dict):
            raise UploadError("The video service returned an unexpected answer.", 502)
        ff_asset = _uuid(started.get("asset_id"))
        ff_version = _uuid(started.get("version_id"))
        upload_id = started.get("upload_id")
        s3_key = started.get("s3_key")
        chunk = _int(started.get("chunk_size_bytes"), 1)
        if not isinstance(upload_id, str) or not upload_id or not isinstance(s3_key, str):
            raise UploadError("The video service returned an unexpected answer.", 502)
        if video is not None and str(video.ff_asset_id) != ff_asset:
            raise UploadError("The video service returned an unexpected answer.", 502)
        try:
            number = _remote_number(user, ff_asset, ff_version)
        except Exception:
            # If FreeFrame created the version but its listing could not be read,
            # discard it. If discard is also unavailable, create a local mirror
            # with the next number so the remote upload remains resumable instead
            # of becoming invisible. A later status refresh copies its real state.
            try:
                _call(
                    user,
                    "POST",
                    "/upload/abort",
                    {"s3_key": s3_key, "upload_id": upload_id, "version_id": ff_version, "discard": True},
                )
            except Exception as exc:
                logger.error(
                    "Could not discard FreeFrame upload %s after number lookup failed; mirroring it: %s",
                    ff_version,
                    exc,
                )
                last_number = (
                    video.versions.order_by("-number").values_list("number", flat=True).first() if video else None
                )
                number = (last_number or 0) + 1
            else:
                raise
        if video is None:
            video = VideoAsset.objects.create(platform_post=locked, ff_asset_id=ff_asset)
        try:
            version = VideoVersion.objects.create(
                video=video,
                number=number,
                ff_version_id=ff_version,
                uploaded_by=user,
                original_filename=name,
                size_bytes=size,
            )
        except Exception:
            # A failed local write still needs to release the just-created remote
            # upload. If FreeFrame refuses, log enough context to reconcile later.
            try:
                _call(
                    user,
                    "POST",
                    "/upload/abort",
                    {"s3_key": s3_key, "upload_id": upload_id, "version_id": ff_version, "discard": True},
                )
            except Exception as exc:
                logger.error("Could not compensate FreeFrame upload %s after local write failed: %s", ff_version, exc)
            raise
    return {
        "version": str(version.pk),
        "number": version.number,
        "state": "resumable",
        "upload_id": upload_id,
        "s3_key": s3_key,
        "chunk_size": chunk,
        "held_parts": [],
        "filename": name,
        "size": size,
    }


def _remote_number(user, ff_asset, ff_version):
    """FreeFrame numbers its versions (v1, v2 ...); the player shows these, so we copy them."""
    versions = _call(user, "GET", f"/assets/{ff_asset}/versions")
    if isinstance(versions, list):
        for item in versions:
            if isinstance(item, dict) and item.get("id") == ff_version:
                return _int(item.get("version_number"), 1)
    raise UploadError("The video service returned an unexpected answer.", 502)


def _own_version(user, workspace, post, version_id, *, uploading=True):
    version = (
        VideoVersion.objects.select_related("video__platform_post")
        .filter(pk=version_id, video__platform_post__post=post, video__platform_post__post__workspace=workspace)
        .first()
    )
    # 404 for strangers' versions as well: nothing tells whether the id exists.
    if version is None or version.uploaded_by_id != user.pk:
        raise UploadError("Upload not found.", 404)
    if uploading and version.status != VideoVersion.Status.UPLOADING:
        raise UploadError("This upload is already finished.", 409)
    return version


def _require_current_editor(user, post):
    membership = WorkspaceMembership.objects.filter(user=user, workspace=post.workspace).first()
    if not can_upload(post, membership):
        raise UploadError("You cannot upload video for this content.", 403)


def resume_info(user, workspace, post, version_id):
    """Which parts are already in the storage, so only the missing ones are sent again."""
    version = _own_version(user, workspace, post, version_id)
    _require_current_editor(user, post)
    info = _call(user, "GET", f"/upload/{version.ff_version_id}/parts")
    if not isinstance(info, dict) or info.get("state") not in {"resumable", "assembled"}:
        raise UploadError("The video service returned an unexpected answer.", 502)
    held = info.get("held_part_numbers")
    if not isinstance(held, list) or not all(isinstance(n, int) and not isinstance(n, bool) for n in held):
        raise UploadError("The video service returned an unexpected answer.", 502)
    if _uuid(info.get("version_id")) != str(version.ff_version_id):
        raise UploadError("The video service returned an unexpected answer.", 502)
    return {
        "version": str(version.pk),
        "number": version.number,
        "state": info["state"],
        "upload_id": str(info.get("upload_id") or ""),
        "s3_key": str(info.get("s3_key") or ""),
        "chunk_size": _int(info.get("chunk_size_bytes"), 1),
        "held_parts": sorted(set(held)),
        "filename": version.original_filename,
        "size": _int(info.get("file_size_bytes"), 1),
    }


def _key_matches(version, s3_key):
    parts = str(s3_key).split("/")
    return (
        len(parts) == 5
        and parts[0] == "raw"
        and parts[2] == str(version.video.ff_asset_id)
        and parts[3] == str(version.ff_version_id)
        and parts[4].startswith("original")
    )


def part_url(user, workspace, post, version_id, *, part, s3_key, upload_id):
    """Step 2: a short-lived signed address for one part, only for the uploader's own version."""
    version = _own_version(user, workspace, post, version_id)
    _require_current_editor(user, post)
    if isinstance(part, bool) or not isinstance(part, int) or not 1 <= part <= MAX_PART_NUMBER:
        raise UploadError("Invalid part number.")
    if not isinstance(upload_id, str) or not upload_id or len(upload_id) > 1024 or not _key_matches(version, s3_key):
        raise UploadError("Invalid upload.", 403)
    signed = _call(
        user, "POST", "/upload/presign-part", {"s3_key": s3_key, "upload_id": upload_id, "part_number": part}
    )
    url = signed.get("presigned_url") if isinstance(signed, dict) else None
    _check_signed_url(url)
    return url


def _check_signed_url(url):
    """The address must lead to our storage and be short-lived; anything else is refused."""
    parsed = urlsplit(url) if isinstance(url, str) else None
    origin = getattr(settings, "JRC_S3_PUBLIC_ORIGIN", "")
    if parsed is None or f"{parsed.scheme}://{parsed.netloc}" != origin:
        raise UploadError("The video service returned an unexpected address.", 502)
    expires = parse_qs(parsed.query).get("X-Amz-Expires", [""])[0]
    if not expires.isdigit() or not 0 < int(expires) <= MAX_PART_URL_SECONDS:
        raise UploadError("The video service returned an address with an unsafe lifetime.", 502)


def complete_upload(user, workspace, post, version_id, *, s3_key, upload_id):
    """Step 3: FreeFrame checks that every part is in the storage and starts the conversion."""
    version = _own_version(user, workspace, post, version_id)
    _require_current_editor(user, post)
    if not isinstance(upload_id, str) or not upload_id or not _key_matches(version, s3_key):
        raise UploadError("Invalid upload.", 403)
    _call(
        user,
        "POST",
        "/upload/complete",
        {
            "s3_key": s3_key,
            "upload_id": upload_id,
            "asset_id": str(version.video.ff_asset_id),
            "version_id": str(version.ff_version_id),
        },
    )
    VideoVersion.objects.filter(pk=version.pk, status=VideoVersion.Status.UPLOADING).update(
        status=VideoVersion.Status.PROCESSING
    )
    return {"version": str(version.pk), "status": VideoVersion.Status.PROCESSING}


def abort_upload(user, workspace, post, version_id, *, s3_key, upload_id):
    """Discard an unfinished upload of one's own."""
    version = _own_version(user, workspace, post, version_id)
    if not isinstance(upload_id, str) or not upload_id or not _key_matches(version, s3_key):
        raise UploadError("Invalid upload.", 403)
    try:
        _call(
            user,
            "POST",
            "/upload/abort",
            {
                "s3_key": s3_key,
                "upload_id": upload_id,
                "version_id": str(version.ff_version_id),
                "discard": True,
            },
        )
    except UploadError as exc:
        # Already removed in FreeFrame (cleaned up as stale): drop our row too.
        if exc.status != 404:
            raise

    # A 204 from /abort means the request was handled, not necessarily that the
    # version was discarded: FreeFrame can discover that completion won a race
    # with this request and leave the assembled version in place. Only remove our
    # mirror after the authoritative version list confirms it is gone.
    remote_asset_missing = False
    try:
        remote_versions = _call(user, "GET", f"/assets/{version.video.ff_asset_id}/versions")
    except UploadError as exc:
        if exc.status != 404:
            raise UploadError(
                "Could not confirm whether the upload was discarded. Refresh and try again.", 503
            ) from None
        # FreeFrame removes an empty asset when its first upload is discarded.
        remote_asset_missing = True
        remote_versions = []
    if not isinstance(remote_versions, list):
        raise UploadError("The video service returned an unexpected answer.", 502)
    remote = next(
        (item for item in remote_versions if isinstance(item, dict) and item.get("id") == str(version.ff_version_id)),
        None,
    )
    if remote is None:
        video = version.video
        version.delete()
        if remote_asset_missing and not video.versions.exists():
            video.delete()
        return {"discarded": True}

    _apply_remote(version, remote)
    version.refresh_from_db()
    return {"discarded": False, "status": version.status}


def _apply_remote(version, remote):
    status = remote.get("processing_status")
    if status not in VideoVersion.Status.values:
        return
    files = remote.get("files") if isinstance(remote.get("files"), list) else []
    media = files[0] if files and isinstance(files[0], dict) else {}

    def number(key, kind):
        value = media.get(key)
        return kind(value) if isinstance(value, int | float) and not isinstance(value, bool) else None

    changes = {
        "status": status,
        "duration_s": number("duration_seconds", float),
        "fps": number("fps", float),
        "width": number("width", int),
        "height": number("height", int),
    }
    remote_number = remote.get("version_number")
    if isinstance(remote_number, int) and not isinstance(remote_number, bool) and remote_number > 0:
        changes["number"] = remote_number
    if status == VideoVersion.Status.READY and version.status != VideoVersion.Status.READY:
        # Only the refresh that really flips the version to ready announces it (F7-B03);
        # a second worker or an open tab updating at the same moment finds it ready already.
        flipped = VideoVersion.objects.filter(pk=version.pk).exclude(status=VideoVersion.Status.READY).update(**changes)
        if flipped:
            version.refresh_from_db()
            notify_video_version(version)
        return
    if any(getattr(version, key) != value for key, value in changes.items()):
        VideoVersion.objects.filter(pk=version.pk).update(**changes)


def _refresh_videos(videos, client_for):
    """Copy the conversion state of unfinished versions from FreeFrame. False when it was unreachable."""
    fresh = True
    for video in videos:
        try:
            remote = client_for(video).request("GET", f"/assets/{video.ff_asset_id}/versions")
        except FreeFrameError as exc:
            logger.info("Status refresh of video %s failed: %s", video.pk, exc)
            fresh = False
            continue
        if not isinstance(remote, list):
            fresh = False
            continue
        by_id = {item.get("id"): item for item in remote if isinstance(item, dict)}
        for version in video.versions.filter(status__in=UNFINISHED):
            item = by_id.get(str(version.ff_version_id))
            if item is None:
                # Removed in FreeFrame (for example a stale upload cleaned up there).
                VideoVersion.objects.filter(pk=version.pk).update(status=VideoVersion.Status.FAILED)
            else:
                _apply_remote(version, item)
    return fresh


def refresh_statuses(user, post):
    """Update unfinished versions of one content from FreeFrame as ``user``. False when unreachable."""
    videos = VideoAsset.objects.filter(platform_post__post=post, versions__status__in=UNFINISHED).distinct()
    client = FreeFrameUserClient(user)
    return _refresh_videos(videos, lambda _video: client)


def refresh_on_open(user, post):
    """Opening the Video tab brings unfinished versions up to date; a failure never blocks the page."""
    try:
        return refresh_statuses(user, post)
    except Exception:  # noqa: BLE001 - the tab still shows the last known state
        logger.exception("Status refresh on opening the Video tab failed for content %s", post.pk)
        return False


def refresh_all_unfinished():
    """Worker job (F5-B08): keep conversion states current even when nobody has the Video tab open."""
    videos = list(VideoAsset.objects.filter(versions__status__in=UNFINISHED).distinct())
    if not videos:
        return 0
    client = FreeFrameClient()
    _refresh_videos(videos, lambda _video: client)
    return len(videos)


def _version_row(version, user):
    resolution = f"{version.width}×{version.height}" if version.width and version.height else ""
    return {
        "id": str(version.pk),
        "ff_version_id": str(version.ff_version_id),
        "is_final": version.video.final_version_id == version.pk,
        "number": version.number,
        "status": version.status,
        "status_label": _label(version),
        "filename": version.original_filename,
        "size_bytes": version.size_bytes,
        "uploaded_at": timezone.localtime(version.uploaded_at).strftime("%b %-d, %Y · %H:%M"),
        "uploaded_by": (
            (version.uploaded_by.display_name or version.uploaded_by.email) if version.uploaded_by else "Former member"
        ),
        "mine": version.uploaded_by_id == user.pk,
        "duration": f"{int(version.duration_s // 60)}:{int(version.duration_s % 60):02d}" if version.duration_s else "",
        "resolution": resolution,
    }


def channel_cards(user, post, membership):
    """Channels of this content with their video versions, for the Video tab and the status feed."""
    from apps.jrc_production.views import _enabled_caption_accounts

    from .player import can_mark_final

    editor = can_upload(post, membership)
    marker = can_mark_final(post, membership)
    project = VideoProject.objects.filter(workspace=post.workspace).first()
    variants = {v.social_account_id: v for v in post.platform_posts.select_related("social_account")}
    videos = {
        video.platform_post_id: video
        for video in VideoAsset.objects.filter(platform_post__post=post)
        .select_related("final_version", "final_by")
        .prefetch_related("versions__uploaded_by")
    }
    cards = []
    for account in _enabled_caption_accounts(post):
        variant = variants.get(account.pk)
        video = videos.get(variant.pk) if variant else None
        versions = [_version_row(v, user) for v in video.versions.all()] if video else []
        final = video.final_version if video and video.final_version_id else None
        cards.append(
            {
                "key": str(variant.pk) if variant else f"new:{account.pk}",
                "label": f"{account.get_platform_display()} · {account.account_name}",
                "can_upload": editor,
                "can_mark_final": marker,
                # The viewer address inside FreeFrame; the version is added by the page (?version=).
                "player_path": f"/projects/{project.ff_project_id}/assets/{video.ff_asset_id}"
                if project and video
                else "",
                "final": (
                    {
                        "version": str(final.pk),
                        "number": final.number,
                        "by": (video.final_by.display_name or video.final_by.email) if video.final_by else "",
                        "at": timezone.localtime(video.final_at).strftime("%b %-d, %Y · %H:%M")
                        if video.final_at
                        else "",
                    }
                    if final
                    else None
                ),
                "versions": versions,
                "unfinished": next((v for v in versions if v["mine"] and v["status"] == "uploading"), None),
            }
        )
    return cards
