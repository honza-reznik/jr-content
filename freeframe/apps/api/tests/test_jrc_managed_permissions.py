"""Managed identities cannot bypass Brightbean or retain revoked sessions."""

import uuid
from unittest.mock import patch, MagicMock

import jwt
import pytest

from apps.api.config import settings
from apps.api.models.user import UserStatus
from apps.api.routers.hls_proxy import create_hls_token, _segment_url_lifetime, MANAGED_MEDIA_SECONDS
from apps.api.services.auth_service import create_access_token


@pytest.mark.parametrize("path,method,body", [
    ("/auth/login", "post", {"email": "test@example.com", "password": "password123"}),
    ("/auth/send-magic-code", "post", {"email": "test@example.com"}),
    ("/auth/verify-magic-code", "post", {"email": "test@example.com", "code": "123456"}),
    ("/auth/set-password", "post", {"password": "password123"}),
    ("/auth/change-password", "patch", {"current_password": "old", "new_password": "password123"}),
    ("/auth/accept-invite", "post", {"token": "old-invite", "name": "Name", "password": "password123"}),
    ("/auth/invite/old-invite", "get", None),
])
def test_managed_account_requires_ticket(client, mock_db, test_user, auth_headers, path, method, body):
    test_user.jrc_managed = True
    mock_db.first.return_value = test_user
    kwargs = {"headers": auth_headers}
    if body is not None:
        kwargs["json"] = body
    with patch("apps.api.routers.auth.store_magic_code") as store:
        response = getattr(client, method)(path, **kwargs)
    assert response.status_code == 403
    assert "JR Content" in response.json()["detail"]
    store.assert_not_called()
    mock_db.commit.assert_not_called()


@pytest.mark.parametrize("admin,managed", [(False, False), (False, True), (True, True)])
def test_only_unmanaged_admin_can_create_projects(client, auth_headers, test_user, mock_db, admin, managed):
    test_user.is_superadmin = admin
    test_user.jrc_managed = managed
    response = client.post("/projects", json={"name": "Forbidden"}, headers=auth_headers)
    assert response.status_code == 403
    mock_db.add.assert_not_called()


@pytest.mark.parametrize("action", ["approve", "reject"])
def test_managed_cannot_approve_even_as_editor(client, auth_headers, test_user, mock_db, action):
    test_user.jrc_managed = True
    response = client.post(f"/assets/{uuid.uuid4()}/{action}",
                           json={"version_id": str(uuid.uuid4())}, headers=auth_headers)
    assert response.status_code == 403
    mock_db.add.assert_not_called()


@pytest.mark.parametrize("status", ["approved", "rejected", "draft"])
def test_managed_cannot_bypass_approval_using_asset_patch(client, auth_headers, test_user, mock_db, status):
    test_user.jrc_managed = True
    response = client.patch(f"/assets/{uuid.uuid4()}", json={"status": status}, headers=auth_headers)
    assert response.status_code == 403
    mock_db.commit.assert_not_called()


def test_admin_cannot_promote_managed_account(client, auth_headers, test_user, mock_db):
    test_user.is_superadmin = True
    managed = MagicMock(jrc_managed=True, deleted_at=None)
    mock_db.first.return_value = managed
    response = client.patch(f"/admin/users/{uuid.uuid4()}/role", json={"is_admin": True}, headers=auth_headers)
    assert response.status_code == 403
    mock_db.commit.assert_not_called()


@pytest.mark.parametrize("method,suffix,body", [
    ("patch", "", {"body": "Forbidden"}),
    ("delete", "", None),
    ("post", "/resolve", None),
    ("post", "/react", {"emoji": "👍"}),
    ("delete", f"/attachments/{uuid.uuid4()}", None),
])
def test_managed_author_cannot_mutate_comments_after_losing_role(
    client, auth_headers, test_user, mock_db, method, suffix, body,
):
    from fastapi import HTTPException

    test_user.jrc_managed = True
    comment = MagicMock(author_id=test_user.id, asset_id=uuid.uuid4())
    mock_db.first.return_value = comment
    asset = MagicMock(project_id=uuid.uuid4())
    kwargs = {"headers": auth_headers}
    if body is not None:
        kwargs["json"] = body
    with (patch("apps.api.routers.comments._get_asset", return_value=asset),
          patch("apps.api.routers.comments.require_asset_project_live"),
          patch("apps.api.routers.comments.require_asset_access"),
          patch("apps.api.routers.comments.require_project_role",
                side_effect=HTTPException(status_code=403, detail="Requires reviewer role"))):
        response = getattr(client, method)(f"/comments/{uuid.uuid4()}{suffix}", **kwargs)
    assert response.status_code == 403
    mock_db.commit.assert_not_called()


@pytest.mark.parametrize("optional", [False, True])
def test_version_change_revokes_access_in_both_auth_paths(test_user, mock_db, optional):
    from fastapi import HTTPException
    from fastapi.security import HTTPAuthorizationCredentials
    from apps.api.middleware.auth import get_current_user, get_optional_user

    token = create_access_token(str(test_user.id), token_version=1)
    credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
    resolver = get_optional_user if optional else get_current_user
    with patch("apps.api.middleware.auth.get_user_by_id", return_value=test_user):
        assert resolver(credentials, mock_db) is test_user
        test_user.token_version = 2
        if optional:
            assert resolver(credentials, mock_db) is None
        else:
            with pytest.raises(HTTPException) as exc:
                resolver(credentials, mock_db)
            assert exc.value.status_code == 401


def test_deactivation_and_reactivation_do_not_restore_old_token(client, test_user, mock_db):
    token = create_access_token(str(test_user.id), token_version=1)
    headers = {"Authorization": f"Bearer {token}"}
    with patch("apps.api.middleware.auth.get_user_by_id", return_value=test_user):
        assert client.get("/auth/me", headers=headers).status_code == 200
        test_user.status = UserStatus.deactivated
        test_user.token_version += 1
        assert client.get("/auth/me", headers=headers).status_code == 401
        test_user.status = UserStatus.active
        assert client.get("/auth/me", headers=headers).status_code == 401


def test_setting_password_revokes_old_tokens_and_returns_new_pair(client, auth_headers, test_user):
    from apps.api.services.auth_service import decode_token

    with patch("apps.api.routers.auth.hash_password", return_value="new-hash"):
        response = client.post("/auth/set-password", json={"password": "password123"}, headers=auth_headers)
    assert response.status_code == 200
    assert test_user.token_version == 2
    assert decode_token(response.json()["access_token"])["ver"] == 2
    assert decode_token(response.json()["refresh_token"])["ver"] == 2


def test_managed_streams_check_membership_and_segments_are_short(client, test_user):
    from fastapi import HTTPException

    token = create_hls_token("processed/video", managed=True, user_id=str(test_user.id),
                             project_id=str(uuid.uuid4()), token_version=1)
    payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    assert payload["uid"] == str(test_user.id)
    assert _segment_url_lifetime(payload) <= MANAGED_MEDIA_SECONDS
    # Even an unexpired playback token is refused on both manifest and segment
    # once its user's live project membership has gone.
    with (patch("apps.api.routers.hls_proxy.get_user_by_id", return_value=test_user),
          patch("apps.api.routers.hls_proxy.require_project_role",
                side_effect=HTTPException(status_code=403, detail="Not a project member"))):
        response = client.get(f"/stream/hls/master.m3u8?token={token}")
        assert response.status_code == 403
        assert client.get(f"/stream/hls/720p/seg.ts?token={token}").status_code == 403


def test_managed_segment_checks_version_and_redirects_for_only_three_minutes(client, test_user):
    token = create_hls_token("processed/video", managed=True, user_id=str(test_user.id),
                             project_id=str(uuid.uuid4()), token_version=1)
    with (patch("apps.api.routers.hls_proxy.get_user_by_id", return_value=test_user),
          patch("apps.api.routers.hls_proxy.require_project_role"),
          patch("apps.api.routers.hls_proxy.generate_presigned_get_url", return_value="/short") as sign):
        response = client.get(f"/stream/hls/720p/seg.ts?token={token}", follow_redirects=False)
        assert response.status_code == 302
        assert response.headers["cache-control"] == "no-store"
        assert sign.call_args.kwargs["expires_in"] == MANAGED_MEDIA_SECONDS
        test_user.token_version = 2
        assert client.get(f"/stream/hls/720p/seg.ts?token={token}").status_code == 401


def test_managed_manifest_keeps_segments_at_authorized_proxy():
    from apps.api.routers.hls_proxy import _rewrite_manifest

    content = _rewrite_manifest("#EXTM3U\nseg001.ts\nparts/seg002.ts\n", "processed/video", "720p/index.m3u8",
                                "opaque-token", proxy_segments=True)
    assert "seg001.ts?token=opaque-token" in content
    assert "parts/seg002.ts?token=opaque-token" in content
    assert "X-Amz" not in content


def test_old_unbound_hls_format_cannot_keep_managed_playback_alive(client):
    from datetime import datetime, timedelta, timezone

    token = jwt.encode({"sub": "hls", "pfx": "processed/video",
                        "exp": datetime.now(timezone.utc) + timedelta(hours=24)},
                       settings.jwt_secret, algorithm=settings.jwt_algorithm)
    assert client.get(f"/stream/hls/master.m3u8?token={token}").status_code == 401


def test_managed_stream_and_download_use_short_lifetime(client, auth_headers, test_user):
    from apps.api.models.asset import AssetType

    test_user.jrc_managed = True
    asset = MagicMock(asset_type=AssetType.video, project_id=uuid.uuid4())
    media = MagicMock(s3_key_processed="processed/video")
    with patch("apps.api.routers.assets._ready_media_file", return_value=(asset, media)):
        response = client.get(f"/assets/{uuid.uuid4()}/stream", headers=auth_headers)
    assert response.status_code == 200
    token = response.json()["url"].split("token=")[1]
    payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    assert _segment_url_lifetime(payload) <= MANAGED_MEDIA_SECONDS
    asset.asset_type = AssetType.audio
    with (patch("apps.api.routers.assets._ready_media_file", return_value=(asset, media)),
          patch("apps.api.routers.assets.generate_presigned_get_url", return_value="/short") as sign):
        assert client.get(f"/assets/{uuid.uuid4()}/stream", headers=auth_headers).status_code == 200
    assert sign.call_args.kwargs["expires_in"] == MANAGED_MEDIA_SECONDS


def test_real_membership_removal_revokes_already_issued_hls_token(client, real_db):
    """Actual Postgres rows and actual permission checks, without mocked membership."""
    from apps.api.database import get_db
    from apps.api.main import app
    from apps.api.models.user import User
    from apps.api.models.project import Project, ProjectMember, ProjectRole
    from apps.api.models.asset import Asset, AssetType

    user = User(id=uuid.uuid4(), email=f"{uuid.uuid4().hex}@example.com", name="Managed",
                jrc_managed=True, token_version=1, status=UserStatus.active)
    real_db.add(user)
    real_db.flush()
    project = Project(id=uuid.uuid4(), name="Private video", created_by=user.id)
    real_db.add(project)
    real_db.flush()
    member = ProjectMember(project_id=project.id, user_id=user.id, role=ProjectRole.reviewer)
    real_db.add(member)
    real_db.flush()
    asset = Asset(id=uuid.uuid4(), project_id=project.id, created_by=user.id, name="Own upload",
                  asset_type=AssetType.video)
    real_db.add(asset)
    real_db.flush()
    token = create_hls_token("processed/video", managed=True, user_id=str(user.id),
                             project_id=str(project.id), token_version=user.token_version)
    app.dependency_overrides[get_db] = lambda: real_db
    headers = {"Authorization": f"Bearer {create_access_token(str(user.id), token_version=1)}"}
    assert client.get(f"/assets/{asset.id}/versions", headers=headers).status_code == 200
    member.role = ProjectRole.viewer
    real_db.flush()
    assert client.post(f"/assets/{asset.id}/comments", headers=headers,
                       json={"version_id": str(uuid.uuid4()), "body": "Forbidden"}).status_code == 403
    url = f"/stream/hls/720p/seg.ts?token={token}"
    with patch("apps.api.routers.hls_proxy.generate_presigned_get_url", return_value="/short") as sign:
        assert client.get(url, follow_redirects=False).status_code == 302
        real_db.delete(member)
        real_db.flush()
        assert client.get(url, follow_redirects=False).status_code == 403
        assert client.get(f"/stream/hls/master.m3u8?token={token}").status_code == 403
        assert client.get(f"/assets/{asset.id}/stream", headers=headers).status_code == 403
        assert client.get(f"/assets/{asset.id}/versions", headers=headers).status_code == 403
        assert client.get(f"/assets/{asset.id}/comments", headers=headers).status_code == 403
        assert sign.call_count == 1
