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


@pytest.mark.parametrize("kind", ["avatar", "project_logo", "instance_logo"])
def test_identity_and_branding_files_require_session_and_live_access(client, live_project, storage, kind):
    from apps.api.models.branding import ProjectBranding
    from apps.api.models.instance_branding import InstanceBranding
    ctx = live_project
    if kind == "avatar":
        key = f"avatars/{ctx.user.id}/photo.png"
        ctx.user.avatar_url = key
    elif kind == "project_logo":
        key = f"branding/{ctx.project.id}/logo/logo.png"
        ctx.db.add(ProjectBranding(project_id=ctx.project.id, logo_s3_key=key))
    else:
        key = "branding/logo-light/logo.png"
        ctx.db.query(InstanceBranding).delete()
        ctx.db.add(InstanceBranding(logo_light_key=key))
    ctx.db.flush()
    assert client.get("/files/" + key, headers=ctx.headers).status_code == 200
    assert client.get("/files/" + key).status_code in (401, 403)
    if kind == "project_logo":
        ctx.member.deleted_at = datetime.now(timezone.utc)
        ctx.db.flush()
        assert client.get("/files/" + key, headers=ctx.headers).status_code == 404


@pytest.mark.parametrize("change,status", [
    ("disabled", 403), ("expired", 410), ("deleted", 404),
    ("password", 403), ("permission", 403), ("visibility", 403),
    ("token", 403), ("download", 403), ("scope", 403), ("membership", 404), ("versions", 404),
])
def test_issued_file_url_rechecks_share_and_membership(client, live_project, storage, monkeypatch, change, status):
    import json
    from datetime import timedelta
    from urllib.parse import urlsplit
    from .test_jrc_live_membership import _link
    ctx = live_project
    link = _link(ctx, "asset")
    cache = {}
    redis = Mock()
    redis.setex.side_effect = lambda key, ttl, value: cache.__setitem__(key, value)
    redis.get.side_effect = cache.get
    monkeypatch.setattr("apps.api.services.redis_service.get_redis", lambda: redis)
    monkeypatch.setattr(files, "get_redis", lambda: redis)
    monkeypatch.setattr("apps.api.middleware.global_rate_limit.check_rate_limit", lambda *a, **kw: (True, 0))
    monkeypatch.setattr(settings, "s3_public_path", "/jrc/storage")
    monkeypatch.setattr(settings, "s3_public_endpoint", "https://jrc.example.invalid")
    response = client.get(f"/share/{link.token}/stream/{ctx.asset.id}?download=true", headers=ctx.headers)
    assert response.status_code == 200, response.text
    issued = response.json()["url"]
    parts = urlsplit(issued)
    url = parts.path.removeprefix("/jrc/files/video")
    url = "/files" + url + "?" + parts.query
    assert "token" not in parts.query and "X-Amz" not in issued
    assert json.loads(next(iter(cache.values())))["session"] is None
    assert client.get(url, headers=ctx.headers).status_code == 200
    assert client.get(url).status_code in (401, 403)
    if change == "versions":
        from apps.api.models.asset import AssetVersion, FileType, MediaFile, ProcessingStatus
        newer = AssetVersion(asset_id=ctx.asset.id, version_number=2, created_by=ctx.user.id,
                             processing_status=ProcessingStatus.ready)
        ctx.db.add(newer)
        ctx.db.flush()
        ctx.db.add(MediaFile(version_id=newer.id, file_type=FileType.video, original_filename="new.mp4",
                            mime_type="video/mp4", file_size_bytes=10, s3_key_raw="raw/new"))
        link.show_versions = False
    elif change == "disabled":
        link.is_enabled = False
    elif change == "expired":
        link.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    elif change == "deleted":
        link.deleted_at = datetime.now(timezone.utc)
    elif change == "download":
        link.allow_download = False
    elif change == "scope":
        from apps.api.models.asset import Asset, AssetType
        other = Asset(project_id=ctx.project.id, name="Other", created_by=ctx.user.id, asset_type=AssetType.video)
        ctx.db.add(other)
        ctx.db.flush()
        link.asset_id = other.id
    elif change == "membership":
        ctx.member.deleted_at = datetime.now(timezone.utc)
    else:
        setattr(link, {"password": "password_hash", "permission": "permission", "visibility": "visibility", "token": "token"}[change],
                {"password": "changed", "permission": "view", "visibility": "secure", "token": uuid.uuid4().hex}[change])
    ctx.db.flush()
    storage[0].reset_mock()
    assert client.get(url, headers=ctx.headers).status_code == status
    storage[0].get_object.assert_not_called()


def test_head_releases_storage_and_capacity(client, live_project, storage):
    response = client.head("/files/" + live_project.media.s3_key_raw, headers=live_project.headers)
    assert response.status_code == 200 and response.content == b""
    assert response.headers["content-length"] == "10"
    assert storage[1][0]._raw_stream.closed
    storage[2].assert_called_once()


@pytest.mark.parametrize("value", ["bytes=0-1,4-5", "bytes=", "other", "bytes=-"])
def test_invalid_range_never_opens_storage(client, live_project, storage, value):
    response = client.get("/files/" + live_project.media.s3_key_raw,
                          headers=dict(live_project.headers, Range=value))
    assert response.status_code == 416
    storage[0].get_object.assert_not_called()
    storage[2].assert_called_once()
