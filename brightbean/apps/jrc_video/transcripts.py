"""Bounded UTF-8 SubRip transcripts, stored privately alongside a video version."""

import logging
import re
import uuid

from django.core.files.base import ContentFile
from django.db import transaction
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404
from django.utils.http import content_disposition_header
from django.views.decorators.http import require_http_methods

from . import uploads
from .models import VideoVersion
from .upload_views import _context, _json, _secure
from .uploads import UploadError

MAX_BYTES = 1024 * 1024
TIMING = re.compile(
    r"([0-9]{2,4}):([0-5][0-9]):([0-5][0-9]),([0-9]{3}) --> ([0-9]{2,4}):([0-5][0-9]):([0-5][0-9]),([0-9]{3})"
)
logger = logging.getLogger(__name__)


def validate_srt(file):
    name = str(file.name).replace("\\", "/").rsplit("/", 1)[-1]
    name = "".join(c for c in name if c.isprintable() and c not in '<>:"|?*').strip(" .")
    if not name.lower().endswith(".srt") or len(name) > 200:
        raise UploadError("Choose an SRT file with a valid name.")
    if file.size > MAX_BYTES:
        raise UploadError("The SRT file must be at most 1 MB.", 413)
    data = file.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise UploadError("The SRT file must be at most 1 MB.", 413)
    try:
        text = data.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n").strip()
    except UnicodeDecodeError:
        raise UploadError("The SRT file must use UTF-8 encoding.") from None
    if not text or any(ord(c) < 32 and c not in "\n\t" for c in text):
        raise UploadError("The file is not valid SRT.")
    for block in re.split(r"\n(?:[ \t]*\n)+", text):
        lines = block.splitlines()
        timing = TIMING.fullmatch(lines[1]) if len(lines) >= 3 else None
        if not timing or not re.fullmatch(r"[0-9]{1,9}", lines[0]) or int(lines[0]) < 1:
            raise UploadError("The file is not valid SRT. Each cue needs a number, timestamps and text.")
        values = list(map(int, timing.groups()))
        start = ((values[0] * 60 + values[1]) * 60 + values[2]) * 1000 + values[3]
        end = ((values[4] * 60 + values[5]) * 60 + values[6]) * 1000 + values[7]
        if end <= start or not "\n".join(lines[2:]).strip():
            raise UploadError("The file is not valid SRT. Check cue times and text.")
    return name, data


def delete_file(storage, name):
    try:
        storage.delete(name)
    except Exception:  # noqa: BLE001 - a committed replacement must remain usable
        logger.exception("Could not remove obsolete video transcript %s", name)


@_secure
@require_http_methods(["GET", "POST"])
@_json
def transcript(request, workspace_id, post_id, version_id):
    _workspace, membership, post = _context(request, workspace_id, post_id)
    versions = VideoVersion.objects.filter(video__platform_post__post=post)
    version = get_object_or_404(versions, pk=version_id)
    if request.method == "GET":
        if not version.transcript:
            raise Http404
        with version.transcript.open("rb") as file:
            data = file.read(MAX_BYTES + 1)
        if request.GET.get("preview") == "1":
            response = JsonResponse({"text": data.decode("utf-8-sig")})
        else:
            response = HttpResponse(data, content_type="application/x-subrip; charset=utf-8")
            response["Content-Disposition"] = content_disposition_header(True, version.transcript_filename)
        response["X-Content-Type-Options"] = "nosniff"
        return response
    if not uploads.can_upload(post, membership):
        raise UploadError("You cannot change transcripts for this content.", 403)
    deleting = request.POST.get("delete") == "1"
    if not deleting:
        file = request.FILES.get("file")
        if file is None:
            raise UploadError("Choose an SRT file.")
        filename, data = validate_srt(file)
    new_key = None
    storage = version.transcript.storage
    try:
        with transaction.atomic():
            version = get_object_or_404(versions.select_for_update(), pk=version_id)
            old_key = version.transcript.name
            if not deleting:
                # Unique object keys make concurrent replacement and rollback safe.
                new_key = storage.save(f"jrc_video_transcripts/{version.pk}/{uuid.uuid4()}.srt", ContentFile(data))
                version.transcript = new_key
                version.transcript_filename = filename
            else:
                version.transcript = ""
                version.transcript_filename = ""
            version.save(update_fields=["transcript", "transcript_filename"])
            if old_key:
                transaction.on_commit(lambda: delete_file(storage, old_key))
    except Exception:
        if new_key:
            delete_file(storage, new_key)
        raise
    return {"filename": version.transcript_filename}
