"""Soukromé objekty FreeFrame pro živý test přes Django session; pouze P1-B03."""
import json
import os
import sys
import uuid

from apps.api.database import SessionLocal
from apps.api.models.asset import Asset, AssetType, AssetVersion, FileType, MediaFile, ProcessingStatus
from apps.api.models.comment import Comment, CommentAttachment
from apps.api.routers.jrc_sync import NAMESPACE
from apps.api.services.s3_service import get_s3_client, generate_presigned_get_url
from apps.api.config import settings

assert os.environ.get("JRC_PROJECT") == "jrc-p1b03"
fixture = json.load(sys.stdin)
owner = uuid.uuid5(NAMESPACE, "account:" + fixture["people"]["owner"]["id"])
project = uuid.uuid5(NAMESPACE, "project:" + fixture["workspace"])
files = []
s3 = get_s3_client()
payload = b"0123456789" * 30000

def store(kind, internal=False, *, prefix=None):
    key = f"{prefix or kind}/{uuid.uuid4()}/{kind}.bin"
    s3.put_object(Bucket=settings.s3_bucket, Key=key, Body=payload, ContentType="application/octet-stream")
    files.append({"url": generate_presigned_get_url(key), "internal": internal})
    return key

with SessionLocal() as db:
    asset = Asset(project_id=project, name="P1-B03", created_by=owner, asset_type=AssetType.video)
    db.add(asset)
    db.flush()
    version = AssetVersion(asset_id=asset.id, version_number=1, created_by=owner, processing_status=ProcessingStatus.ready)
    db.add(version)
    db.flush()
    media = MediaFile(version_id=version.id, file_type=FileType.video, original_filename="Final.mp4",
        mime_type="video/mp4", file_size_bytes=len(payload), s3_key_raw=store("original"),
        s3_key_processed=store("preview"), s3_key_download=store("final"), s3_key_thumbnail=store("thumbnail"))
    db.add(media)
    for visibility in ("public", "internal"):
        comment = Comment(asset_id=asset.id, version_id=version.id, author_id=owner, body=visibility, visibility=visibility)
        db.add(comment)
        db.flush()
        key = store("attachment", visibility == "internal", prefix=f"comment-attachments/{comment.id}")
        name = "a" * 251 + ".txt" if visibility == "public" else "Attachment.txt"
        db.add(CommentAttachment(comment_id=comment.id, s3_key=key,
            original_filename=name, file_type="text/plain", file_size_bytes=len(payload)))
        if visibility == "public":
            fixture["long_attachment"] = generate_presigned_get_url(key)
    db.commit()
    fixture["files"].extend(files)
    fixture["video_range"] = generate_presigned_get_url(media.s3_key_raw)
    fixture["video_download"] = generate_presigned_get_url(media.s3_key_download, download_filename="Final.mp4")
print(json.dumps(fixture))
