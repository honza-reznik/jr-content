"""Security regressions: change real memberships while the same JWT stays valid."""

import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from types import SimpleNamespace
from unittest.mock import patch
import uuid

import jwt
import pytest
from sqlalchemy import text

from apps.api.config import settings
from apps.api.database import get_db
from apps.api.main import app
from apps.api.models.activity import Mention, Notification, NotificationType
from apps.api.models.asset import Asset, AssetType, AssetVersion, FileType, MediaFile, ProcessingStatus
from apps.api.models.comment import Comment, CommentAttachment
from apps.api.models.folder import Folder
from apps.api.models.project import Project, ProjectMember, ProjectRole
from apps.api.models.share import AssetShare, ShareLink, ShareLinkItem, SharePermission
from apps.api.models.user import User, UserStatus
from apps.api.services.auth_service import create_access_token, create_refresh_token
from apps.api.services.permissions import project_events_denial, EVENTS_ACCESS_REVOKED


@pytest.fixture(autouse=True)
def isolate_request_limits():
    # These tests deliberately repeat many requests from one TestClient IP.
    # Rate limits have separate tests; do not consume the live Redis counters.
    with patch("apps.api.middleware.rate_limit.check_rate_limit", return_value=(True, 0)):
        yield


@pytest.fixture
def live_project(client, real_db):
    user = User(email=f"{uuid.uuid4().hex}@example.com", name="Managed", jrc_managed=True,
                token_version=1, status=UserStatus.active)
    real_db.add(user)
    real_db.flush()
    project = Project(name="JR Content", created_by=user.id, jrc_managed=True, is_public=True)
    real_db.add(project)
    real_db.flush()
    member = ProjectMember(project_id=project.id, user_id=user.id, role=ProjectRole.editor)
    folder = Folder(project_id=project.id, name="Footage", created_by=user.id)
    real_db.add_all([member, folder])
    real_db.flush()
    asset = Asset(project_id=project.id, folder_id=folder.id, name="Private footage", created_by=user.id,
                  assignee_id=user.id, due_date=datetime.now(timezone.utc) + timedelta(days=1),
                  asset_type=AssetType.video)
    real_db.add(asset)
    real_db.flush()
    version = AssetVersion(asset_id=asset.id, version_number=1, created_by=user.id,
                           processing_status=ProcessingStatus.ready, upload_id="held-upload",
                           chunk_size_bytes=5 * 1024 * 1024)
    real_db.add(version)
    real_db.flush()
    media = MediaFile(version_id=version.id, file_type=FileType.video, original_filename="clip.mp4",
                      mime_type="video/mp4", file_size_bytes=123, s3_key_raw=f"raw/{asset.id}",
                      s3_key_processed=f"processed/{asset.id}", s3_key_thumbnail=f"thumb/{asset.id}")
    real_db.add(media)
    real_db.flush()
    app.dependency_overrides[get_db] = lambda: real_db
    headers = {"Authorization": f"Bearer {create_access_token(str(user.id), token_version=1)}"}
    return SimpleNamespace(user=user, project=project, member=member, folder=folder, asset=asset,
                           version=version, media=media, headers=headers, db=real_db)


def _remove(ctx):
    ctx.member.deleted_at = datetime.now(timezone.utc)
    ctx.db.flush()


def _link(ctx, scope):
    targets = {"asset": {"asset_id": ctx.asset.id}, "folder": {"folder_id": ctx.folder.id},
               "project": {"project_id": ctx.project.id}, "multi": {"project_id": ctx.project.id}}
    link = ShareLink(token=uuid.uuid4().hex, created_by=ctx.user.id, visibility="public",
                     permission=SharePermission.comment, allow_download=True, **targets[scope])
    ctx.db.add(link)
    ctx.db.flush()
    if scope == "multi":
        ctx.db.add(ShareLinkItem(share_link_id=link.id, asset_id=ctx.asset.id))
        ctx.db.flush()
    return link


def _share_requests(ctx, link):
    root = f"/share/{link.token}"
    aid = ctx.asset.id
    return [
        ("get", root, None),
        ("post", root + "/verify", {"password": "old-password"}),
        ("get", root + "/assets", None),
        ("get", root + f"/stream/{aid}", None),
        ("get", root + f"/stream/{aid}?download=true", None),
        ("get", root + f"/downloads/{aid}", None),
        ("get", root + f"/thumbnail/{aid}", None),
        ("get", root + f"/assets/{aid}/versions", None),
        ("get", root + f"/comments?asset_id={aid}&version_id={ctx.version.id}", None),
        ("post", root + "/comment", {"asset_id": str(aid), "version_id": str(ctx.version.id),
                                     "body": "Must stay private", "guest_name": "Guest"}),
    ]


def _request(client, method, path, body, headers=None):
    return getattr(client, method)(path, headers=headers or {}, **({"json": body} if body else {}))


@pytest.mark.parametrize("scope", ["asset", "folder", "project", "multi"])
def test_existing_public_jrc_shares_require_membership_on_every_content_endpoint(client, live_project, scope):
    ctx = live_project
    link = _link(ctx, scope)
    # Links existed before this request; neither public visibility, creator status,
    # password sessions nor possession of their token replace a current membership.
    with patch("apps.api.routers.share.generate_presigned_get_url", return_value="/signed") as sign:
        for method, path, body in _share_requests(ctx, link):
            response = _request(client, method, path, body)
            assert response.status_code == 403, (method, path, response.text)
        sign.assert_not_called()
        assert client.get(f"/share/{link.token}", headers=ctx.headers).status_code == 200
        _remove(ctx)
        sign.reset_mock()
        for method, path, body in _share_requests(ctx, link):
            path += ("&" if "?" in path else "?") + "share_session=old-session"
            response = _request(client, method, path, body, ctx.headers)
            assert response.status_code == 403, (method, path, response.text)
        sign.assert_not_called()
        assert client.get("/auth/me", headers=ctx.headers).status_code == 200


@pytest.mark.parametrize("managed,managed_project", [(True, True), (False, True), (True, False)])
def test_share_role_and_hls_rechecked_for_managed_project_even_for_local_member(
    client, live_project, managed, managed_project,
):
    ctx = live_project
    ctx.user.jrc_managed = managed
    ctx.project.jrc_managed = managed_project
    ctx.db.flush()
    link = _link(ctx, "asset")
    with patch("apps.api.routers.share.generate_presigned_get_url", return_value="/signed") as media_sign:
        response = client.get(f"/share/{link.token}", headers=ctx.headers)
        assert response.status_code == 200, response.text
        hls_url = response.json()["asset"]["stream_url"]
        token = hls_url.split("token=")[1]
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
        assert payload["uid"] == str(ctx.user.id)
        media_sign.reset_mock()
        download = client.get(f"/share/{link.token}/stream/{ctx.asset.id}?download=true", headers=ctx.headers)
        assert download.status_code == 200, download.text
        download_signature = next(call for call in media_sign.call_args_list if call.args[0] == ctx.media.s3_key_raw)
        assert download_signature.kwargs["expires_in"] <= 180
        segment = f"/stream/hls/720p/seg.ts?token={token}"
        with patch("apps.api.routers.hls_proxy.generate_presigned_get_url", return_value="/signed") as sign:
            assert client.get(segment, follow_redirects=False).status_code == 302
            assert sign.call_args.kwargs["expires_in"] <= 180
            # This was a commenting link and the caller is also its creator.
            ctx.member.role = ProjectRole.viewer
            ctx.db.flush()
            assert client.get(f"/share/{link.token}", headers=ctx.headers).status_code == 200
            response = client.post(f"/share/{link.token}/comment", headers=ctx.headers,
                                   json={"version_id": str(ctx.version.id), "body": "Forbidden"})
            assert response.status_code == 403
            _remove(ctx)
            assert client.get(segment, follow_redirects=False).status_code == 403
            assert client.get(hls_url).status_code == 403
            assert sign.call_count == 1


def test_valid_share_password_session_does_not_replace_membership(client, live_project):
    ctx = live_project
    creator = User(email=f"{uuid.uuid4().hex}@example.com", name="Other creator")
    ctx.db.add(creator)
    ctx.db.flush()
    link = _link(ctx, "asset")
    link.created_by = creator.id
    link.password_hash = "existing-password-hash"
    ctx.db.flush()
    path = f"/share/{link.token}/stream/{ctx.asset.id}?share_session=valid-session"
    with patch("apps.api.services.permissions.verify_share_session", return_value=True) as session:
        assert client.get(path, headers=ctx.headers).status_code == 200
        session.assert_called_once_with(link.token, "valid-session")
        session.reset_mock()
        _remove(ctx)
        assert client.get(path, headers=ctx.headers).status_code == 403
        assert client.get(path).status_code == 403
        session.assert_not_called()


def test_blocked_account_cannot_fall_back_to_anonymous_share(client, live_project):
    ctx = live_project
    link = _link(ctx, "asset")
    ctx.user.status = UserStatus.deactivated
    ctx.db.flush()
    for method, path, body in _share_requests(ctx, link):
        assert _request(client, method, path, body, ctx.headers).status_code == 401


@pytest.mark.parametrize("scope", ["asset", "folder", "project", "multi"])
def test_expired_share_session_returns_401_and_real_refresh_restores_access(client, live_project, scope):
    ctx = live_project
    link = _link(ctx, scope)
    expired = jwt.encode({"sub": str(ctx.user.id), "type": "access", "ver": 1,
                          "exp": datetime.now(timezone.utc) - timedelta(seconds=1)},
                         settings.jwt_secret, algorithm=settings.jwt_algorithm)
    for method, path, body in _share_requests(ctx, link):
        response = _request(client, method, path, body, {"Authorization": f"Bearer {expired}"})
        assert response.status_code == 401, (method, path, response.text)
    refreshed = client.post("/auth/refresh", json={"refresh_token": create_refresh_token(str(ctx.user.id))})
    assert refreshed.status_code == 200, refreshed.text
    headers = {"Authorization": f"Bearer {refreshed.json()['access_token']}"}
    assert client.get(f"/share/{link.token}", headers=headers).status_code == 200
    _remove(ctx)
    assert client.get(f"/share/{link.token}", headers=headers).status_code == 403
    assert client.get(f"/share/{link.token}").status_code == 403


def _signed_urls(value):
    if isinstance(value, dict):
        for child in value.values():
            yield from _signed_urls(child)
    elif isinstance(value, list):
        for child in value:
            yield from _signed_urls(child)
    elif isinstance(value, str) and "X-Amz-Signature=" in value:
        yield value


@pytest.mark.parametrize("scope", ["asset", "folder", "project", "multi"])
def test_previews_and_attachments_have_short_real_signatures_and_cannot_be_renewed_after_removal(
    client, live_project, scope,
):
    ctx = live_project
    link = _link(ctx, scope)
    comment = Comment(asset_id=ctx.asset.id, version_id=ctx.version.id, author_id=ctx.user.id, body="Private")
    ctx.db.add(comment)
    ctx.db.flush()
    ctx.db.add(CommentAttachment(comment_id=comment.id, file_type="text/plain", s3_key=f"attachment/{comment.id}",
                                 original_filename="private.txt", file_size_bytes=7))
    ctx.project.poster_s3_key = ctx.media.s3_key_thumbnail
    ctx.db.flush()
    root = f"/share/{link.token}"
    paths = [root + f"/thumbnail/{ctx.asset.id}", root + f"/stream/{ctx.asset.id}",
             root + f"/comments?asset_id={ctx.asset.id}&version_id={ctx.version.id}",
             f"/assets/{ctx.asset.id}/comments", f"/projects/{ctx.project.id}/assets",
             f"/assets/{ctx.asset.id}", "/me/assets", f"/projects/{ctx.project.id}"]
    paths.append(root if scope == "asset" else root + "/assets")
    # The fixture stores a video with processed/raw keys; HLS is separate and
    # checked elsewhere. Every S3 URL returned here is a preview or attachment.
    for path in paths:
        response = client.get(path, headers=ctx.headers)
        assert response.status_code == 200, (path, response.text)
        urls = list(_signed_urls(response.json()))
        assert urls, (path, response.text)
        for url in urls:
            query = parse_qs(urlparse(url).query)
            assert query["X-Amz-Expires"] == ["180"], (path, urlparse(url).path)
    _remove(ctx)
    # Removal happens while those addresses are still valid. Already issued
    # S3 capabilities expire within 180 s; the API cannot issue replacements.
    for path in paths:
        response = client.get(path, headers=ctx.headers)
        assert response.status_code == 403 or response.json() == [], (path, response.text)
        assert list(_signed_urls(response.json())) == []


@pytest.mark.parametrize("change", ["viewer", "removed"])
@pytest.mark.parametrize("operation", ["presign", "resume", "complete"])
def test_inflight_upload_requires_current_editor_role(client, live_project, change, operation):
    ctx = live_project
    ctx.version.processing_status = ProcessingStatus.uploading
    ctx.db.flush()
    if operation == "presign":
        method, path, body = "post", "/upload/presign-part", {
            "s3_key": ctx.media.s3_key_raw, "upload_id": ctx.version.upload_id, "part_number": 1}
    elif operation == "resume":
        method, path, body = "get", f"/upload/{ctx.version.id}/parts", None
    else:
        method, path, body = "post", "/upload/complete", {
            "s3_key": ctx.media.s3_key_raw, "upload_id": ctx.version.upload_id,
            "asset_id": str(ctx.asset.id), "version_id": str(ctx.version.id)}
    # Exercise the real permission checks and routes, mock only the storage boundary.
    with (patch("apps.api.routers.upload.presign_upload_part", return_value="/part") as sign,
          patch("apps.api.routers.upload.list_upload_parts", return_value=[]) as listing,
          patch("apps.api.routers.upload.complete_multipart_upload") as complete,
          patch("apps.api.routers.upload._trigger_processing") as process):
        before = _request(client, method, path, body, ctx.headers)
        assert before.status_code in (200, 409), before.text
        if operation == "complete":
            assert before.status_code == 409  # No parts yet; role gate was passed.
        else:
            assert before.status_code == 200
        for call in (sign, listing, complete, process):
            call.reset_mock()
        if change == "viewer":
            ctx.member.role = ProjectRole.viewer
            ctx.db.flush()
        else:
            _remove(ctx)
        denied = _request(client, method, path, body, ctx.headers)
        assert denied.status_code == 403, denied.text
        for call in (sign, listing, complete, process):
            call.assert_not_called()
        assert ctx.version.processing_status == ProcessingStatus.uploading
        assert client.get("/auth/me", headers=ctx.headers).status_code == 200


@pytest.mark.parametrize("change", ["editor", "viewer", "removed"])
@pytest.mark.parametrize("assembled", [True, False])
def test_abort_recovery_requires_live_editor_but_cleanup_remains_available(
    client, live_project, change, assembled,
):
    ctx = live_project
    ctx.version.processing_status = ProcessingStatus.uploading
    if change == "viewer":
        ctx.member.role = ProjectRole.viewer
    elif change == "removed":
        _remove(ctx)
    ctx.db.flush()
    body = {"s3_key": ctx.media.s3_key_raw, "upload_id": ctx.version.upload_id,
            "version_id": str(ctx.version.id)}
    with (patch("apps.api.routers.upload.abort_multipart_upload") as abort,
          patch("apps.api.routers.upload._already_assembled", return_value=assembled),
          patch("apps.api.routers.upload._trigger_processing") as process):
        response = client.post("/upload/abort", headers=ctx.headers, json=body)
        abort.assert_called_once()
        if assembled and change != "editor":
            assert response.status_code == 403, response.text
            assert ctx.version.processing_status == ProcessingStatus.uploading
            process.assert_not_called()
        else:
            assert response.status_code == 204, response.text
            expected = ProcessingStatus.processing if assembled else ProcessingStatus.failed
            assert ctx.version.processing_status == expected
            assert process.call_count == int(assembled)
        assert client.get("/auth/me", headers=ctx.headers).status_code == 200


@pytest.mark.parametrize("asset_filter", [None, "owned", "shared", "mentioned", "assigned", "due_soon"])
def test_personal_feeds_require_membership_before_pagination_and_signing(client, live_project, asset_filter):
    ctx = live_project
    comment = Comment(asset_id=ctx.asset.id, version_id=ctx.version.id, author_id=ctx.user.id, body="Private")
    ctx.db.add(comment)
    ctx.db.flush()
    ctx.db.add_all([
        AssetShare(asset_id=ctx.asset.id, shared_by=ctx.user.id, shared_with_user_id=ctx.user.id),
        Mention(comment_id=comment.id, mentioned_user_id=ctx.user.id),
        Notification(user_id=ctx.user.id, asset_id=ctx.asset.id, comment_id=comment.id, type=NotificationType.mention),
    ])
    ctx.db.flush()
    path = "/me/assets?limit=1" + (f"&filter={asset_filter}" if asset_filter else "")
    with patch("apps.api.routers.assets.generate_presigned_get_url", return_value="/signed") as sign:
        assert len(client.get(path, headers=ctx.headers).json()) == 1
        assert len(client.get("/me/folders", headers=ctx.headers).json()) == 1
        assert len(client.get("/me/notifications", headers=ctx.headers).json()) == 1
        ctx.member.role = ProjectRole.viewer
        ctx.db.flush()
        assert len(client.get(path, headers=ctx.headers).json()) == 1
        _remove(ctx)
        sign.reset_mock()
        for endpoint in (path, "/me/folders", "/me/notifications"):
            response = client.get(endpoint, headers=ctx.headers)
            assert response.status_code == 200, response.text
            assert response.json() == []
        sign.assert_not_called()


def test_feed_pagination_and_notification_updates_only_use_accessible_projects(client, live_project):
    ctx = live_project
    now = datetime.now(timezone.utc)
    other = Project(name="Still accessible", created_by=ctx.user.id, jrc_managed=True)
    ctx.db.add(other)
    ctx.db.flush()
    ctx.db.add(ProjectMember(project_id=other.id, user_id=ctx.user.id, role=ProjectRole.viewer))
    accessible = Asset(project_id=other.id, name="Older accessible footage", created_by=ctx.user.id,
                       asset_type=AssetType.video, created_at=now - timedelta(days=1))
    ctx.asset.created_at = now
    ctx.db.add(accessible)
    ctx.db.flush()
    allowed = Notification(user_id=ctx.user.id, asset_id=accessible.id,
                           type=NotificationType.assignment, created_at=now - timedelta(days=1))
    denied = [Notification(user_id=ctx.user.id, asset_id=ctx.asset.id,
                           type=NotificationType.assignment, created_at=now) for _ in range(50)]
    ctx.db.add_all([allowed, *denied])
    ctx.db.flush()
    _remove(ctx)
    # If filtering happens after LIMIT, the forbidden newer records consume the page.
    with patch("apps.api.routers.assets.generate_presigned_get_url") as sign:
        page = client.get("/me/assets?filter=owned&limit=1", headers=ctx.headers)
        assert page.status_code == 200, page.text
        assert [a["id"] for a in page.json()] == [str(accessible.id)]
        sign.assert_not_called()
    page = client.get("/me/notifications?unread_only=true", headers=ctx.headers)
    assert page.status_code == 200, page.text
    assert [n["id"] for n in page.json()] == [str(allowed.id)]
    assert client.post(f"/me/notifications/{denied[0].id}/read", headers=ctx.headers).status_code == 404
    assert client.post("/me/notifications/read-all", headers=ctx.headers).status_code == 204
    ctx.db.expire_all()
    assert allowed.read is True
    assert all(n.read is False for n in denied)
    # A restored membership permits the single-item update again.
    ctx.member.deleted_at = None
    ctx.db.flush()
    assert client.post(f"/me/notifications/{denied[0].id}/read", headers=ctx.headers).status_code == 204
    ctx.db.expire_all()
    assert denied[0].read is True


def test_public_project_and_open_events_cannot_bypass_revoked_managed_membership(client, live_project):
    ctx = live_project
    assert client.get(f"/projects/{ctx.project.id}", headers=ctx.headers).status_code == 200
    assert project_events_denial(ctx.db, ctx.project.id, ctx.user.id, 1) is None
    ctx.member.role = ProjectRole.viewer
    ctx.db.flush()
    assert project_events_denial(ctx.db, ctx.project.id, ctx.user.id, 1) is None
    _remove(ctx)
    assert client.get(f"/projects/{ctx.project.id}", headers=ctx.headers).status_code == 403
    assert client.get(f"/events/{ctx.project.id}", headers=ctx.headers).status_code == 403
    # The same predicate is used periodically while an already-open SSE stream runs.
    assert project_events_denial(ctx.db, ctx.project.id, ctx.user.id, 1) == EVENTS_ACCESS_REVOKED
    ctx.user.jrc_managed = False
    ctx.db.flush()
    assert project_events_denial(ctx.db, ctx.project.id, ctx.user.id, 1) is None
    assert client.get(f"/projects/{ctx.project.id}", headers=ctx.headers).status_code == 200


def test_migration_protects_legacy_projects_and_existing_links(client, live_project, monkeypatch):
    ctx = live_project
    legacy = Project(id=uuid.uuid5(uuid.NAMESPACE_URL, uuid.uuid4().hex), name="Legacy JR",
                     created_by=ctx.user.id, jrc_managed=False)
    ctx.db.add(legacy)
    local = Project(name="Local FreeFrame project", created_by=ctx.user.id, jrc_managed=False)
    ctx.db.add(local)
    ctx.db.flush()
    link = ShareLink(project_id=legacy.id, token=uuid.uuid4().hex, created_by=ctx.user.id, visibility="public")
    ctx.db.add(link)
    ctx.db.flush()
    path = Path(__file__).resolve().parents[1] / "alembic/versions/jrc_f5b04_managed_projects.py"
    spec = importlib.util.spec_from_file_location("jrc_project_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    monkeypatch.setattr(migration.op, "add_column", lambda *args, **kwargs: None)
    monkeypatch.setattr(migration.op, "execute", lambda sql: ctx.db.execute(text(sql)))
    migration.upgrade()
    ctx.db.expire_all()
    assert legacy.jrc_managed is True
    assert local.jrc_managed is False
    assert client.get(f"/share/{link.token}").status_code == 403
    assert client.get(f"/share/{link.token}", headers=ctx.headers).status_code == 403


def test_pre_review_unbound_share_hls_token_is_revoked(client):
    token = jwt.encode({"sub": "hls", "v": 2, "pfx": "processed/legacy-share",
                        "exp": datetime.now(timezone.utc) + timedelta(hours=24)},
                       settings.jwt_secret, algorithm=settings.jwt_algorithm)
    assert client.get(f"/stream/hls/master.m3u8?token={token}").status_code == 401
