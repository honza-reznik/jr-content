"""P1-B03: real membership/visibility rows, private streaming transport."""
from datetime import datetime, timezone
from io import BytesIO
from unittest.mock import Mock
import uuid

import pytest
from botocore.response import StreamingBody

from apps.api.config import settings
from apps.api.models.comment import Comment, CommentAttachment
from apps.api.models.project import ProjectRole
from apps.api.routers import files
from apps.api.services.s3_service import generate_presigned_get_url
from .test_jrc_live_membership import live_project, isolate_request_limits  # noqa: F401


@pytest.fixture
def storage(monkeypatch):
    s3 = Mock()
    release = Mock()
    monkeypatch.setattr(files.hls_limits, "acquire", lambda identity: release)
    bodies = []
    def get(**params):
        data = b"0123456789"
        obj = {"ContentType": "video/mp4"}
        if params.get("Range") == "bytes=2-5":
            data = b"2345"
            obj["ContentRange"] = "bytes 2-5/10"
        body = StreamingBody(BytesIO(data), len(data))
        bodies.append(body)
        return dict(obj, Body=body, ContentLength=len(data))
    s3.get_object.side_effect = get
    monkeypatch.setattr(files, "_stream_s3_client", lambda: s3)
    return s3, bodies, release


@pytest.mark.parametrize("kind", ["raw", "processed", "download", "thumbnail", "poster", "attachment"])
def test_each_object_checks_membership_on_every_request(client, live_project, storage, kind):
    ctx = live_project
    key = f"private/{uuid.uuid4()}"
    if kind == "poster":
        ctx.project.poster_s3_key = key
    elif kind == "attachment":
        comment = Comment(asset_id=ctx.asset.id, version_id=ctx.version.id, author_id=ctx.user.id, body="public", visibility="public")
        ctx.db.add(comment)
        ctx.db.flush()
        ctx.db.add(CommentAttachment(comment_id=comment.id, s3_key=key, original_filename="náhled.png", file_type="image/png", file_size_bytes=10))
    else:
        setattr(ctx.media, f"s3_key_{kind}", key)
    ctx.db.flush()
    url = "/files/" + key
    response = client.get(url, headers=ctx.headers)
    assert response.status_code == 200
    assert response.content == b"0123456789"
    assert response.headers["cache-control"] == "private, no-store"
    assert client.get(url).status_code in (401, 403)
    ctx.member.deleted_at = datetime.now(timezone.utc)
    ctx.db.flush()
    assert client.get(url, headers=ctx.headers).status_code == 404


@pytest.mark.parametrize("parent_internal", [True, False])
def test_client_cannot_read_internal_attachment(client, live_project, storage, parent_internal):
    ctx = live_project
    parent = Comment(asset_id=ctx.asset.id, version_id=ctx.version.id, author_id=ctx.user.id, body="internal", visibility="internal")
    ctx.db.add(parent)
    ctx.db.flush()
    comment = parent
    if parent_internal:
        comment = Comment(asset_id=ctx.asset.id, version_id=ctx.version.id, author_id=ctx.user.id, parent_id=parent.id, body="reply", visibility="public")
        ctx.db.add(comment)
        ctx.db.flush()
    key = f"attachments/{comment.id}"
    ctx.db.add(CommentAttachment(comment_id=comment.id, s3_key=key, original_filename="private.png", file_type="image/png", file_size_bytes=10))
    ctx.db.flush()
    assert client.get("/files/" + key, headers=ctx.headers).status_code == 200
    ctx.member.role = ProjectRole.reviewer
    ctx.db.flush()
    assert client.get("/files/" + key, headers=ctx.headers).status_code == 404


def test_range_and_download_filename(client, live_project, storage):
    response = client.get("/files/" + live_project.media.s3_key_raw,
                          params={"filename": "Ella-final.mp4"}, headers=dict(live_project.headers, Range="bytes=2-5"))
    assert response.status_code == 206
    assert response.content == b"2345"
    assert response.headers["content-range"] == "bytes 2-5/10"
    assert "Ella-final.mp4" in response.headers["content-disposition"]
    storage[2].assert_called()


def test_production_urls_never_contain_a_read_signature(monkeypatch):
    monkeypatch.setattr(settings, "s3_public_path", "/jrc/storage")
    monkeypatch.setattr(settings, "s3_public_endpoint", "https://jrc.example.invalid")
    url = generate_presigned_get_url("original/a b.mp4", download_filename="Final.mp4")
    assert url == "https://jrc.example.invalid/jrc/files/video/original/a%20b.mp4?filename=Final.mp4"
