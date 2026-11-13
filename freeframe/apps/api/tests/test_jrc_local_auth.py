"""JRC disables local sign-in before looking up identities; private sync survives."""
from unittest.mock import patch

import pytest

from apps.api.config import settings


@pytest.fixture(autouse=True)
def jrc_mode(monkeypatch):
    monkeypatch.setattr(settings, "jrc_mode", "prod")
    monkeypatch.setenv("JRC_FF_SYNC_EMAIL", "sync@example.com")


@pytest.mark.parametrize("method,path,body", [
    ("post", "/auth/send-magic-code", {"email": "{email}"}),
    ("post", "/auth/verify-magic-code", {"email": "{email}", "code": "123456"}),
    ("post", "/auth/login", {"email": "{email}", "password": "password123"}),
    ("post", "/auth/set-password", {"password": "password123"}),
    ("post", "/auth/accept-invite", {"token": "invite", "name": "Name", "password": "password123"}),
    ("get", "/auth/invite/unknown", None),
    ("patch", "/auth/change-password", {"current_password": "old", "new_password": "password123"}),
])
def test_public_local_auth_is_uniform(client, mock_db, test_user, method, path, body):
    responses = []
    for email, user in [("managed@example.com", test_user),
                        ("local@example.com", test_user), ("unknown@example.com", None),
                        ("sync@example.com", test_user)]:
        test_user.jrc_managed = email.startswith("managed")
        mock_db.first.return_value = user
        payload = {key: value.replace("{email}", email) for key, value in body.items()} if body else None
        with patch("apps.api.routers.auth.get_user_by_email") as lookup, \
                patch("apps.api.routers.auth.store_magic_code") as store:
            response = client.request(method, path, json=payload, headers={"X-Real-IP": "127.0.0.1"})
        responses.append((response.status_code, response.json()))
        lookup.assert_not_called()
        store.assert_not_called()
    assert responses == [(403, {"detail": "Sign in through JR Content."})] * 4
    mock_db.query.assert_not_called()
    mock_db.commit.assert_not_called()


def test_private_sync_password_login_survives(client, test_user):
    with patch("apps.api.routers.auth.get_user_by_email", return_value=test_user), \
            patch("apps.api.routers.auth.verify_password", return_value=True):
        response = client.post("/auth/login", json={"email": "sync@example.com", "password": "password123"})
    assert response.status_code == 200
    assert response.json()["access_token"]


def test_private_login_cannot_sign_in_other_users(client):
    with patch("apps.api.routers.auth.get_user_by_email") as lookup:
        response = client.post("/auth/login", json={"email": "local@example.com", "password": "password123"})
    assert response.status_code == 403
    lookup.assert_not_called()


def test_sso_refresh_and_me_still_work(client, auth_headers, test_user):
    from apps.api.services.auth_service import create_refresh_token

    assert client.get("/auth/me", headers=auth_headers).status_code == 200
    with patch("apps.api.routers.auth.get_user_by_id", return_value=test_user):
        response = client.post("/auth/refresh", json={"refresh_token": create_refresh_token(str(test_user.id))})
    assert response.status_code == 200


def test_logout_revokes_access_refresh_and_hls(client, real_db):
    import uuid
    from apps.api.database import get_db
    from apps.api.main import app
    from apps.api.models.user import User, UserStatus
    from apps.api.services.auth_service import create_access_token, create_refresh_token
    from apps.api.routers.hls_proxy import create_hls_token, hls_session_id

    user = User(id=uuid.uuid4(), email=f"logout-{uuid.uuid4()}@example.com", name="Logout test",
                status=UserStatus.active, jrc_managed=True, token_version=1)
    real_db.add(user)
    real_db.flush()
    user_id = user.id
    access = create_access_token(str(user_id), token_version=1)
    refresh = create_refresh_token(str(user_id), token_version=1)
    hls = create_hls_token("processed/logout-test", managed=True, user_id=str(user_id),
                           project_id=str(uuid.uuid4()), token_version=1)
    app.dependency_overrides[get_db] = lambda: real_db
    headers = {"Authorization": f"Bearer {access}"}
    assert client.get("/auth/me", headers=headers).status_code == 200
    response = client.post("/auth/jrc-logout", headers=headers)
    assert response.status_code == 204
    assert response.headers["cache-control"] == "no-store"
    real_db.expire_all()
    assert real_db.get(User, user_id).token_version == 2
    assert client.get("/auth/me", headers=headers).status_code == 401
    assert client.post("/auth/refresh", json={"refresh_token": refresh}).status_code == 401
    path = f"/stream/hls/{hls_session_id('processed/logout-test')}/master.m3u8"
    assert client.get(path, headers={"Cookie": f"ff_hls={hls}"}).status_code == 401


def test_logout_requires_managed_identity(client, auth_headers, test_user, mock_db):
    response = client.post("/auth/jrc-logout", headers=auth_headers)
    assert response.status_code == 403
    mock_db.commit.assert_not_called()


def test_unknown_new_auth_route_is_forbidden_in_jrc_mode():
    """The policy is an allow list: a route FreeFrame adds later is closed by default."""
    from fastapi import APIRouter, Depends, FastAPI
    from fastapi.testclient import TestClient

    from apps.api.routers.auth import JRC_ALLOWED_AUTH_ROUTES, require_jrc_auth_policy

    router = APIRouter(prefix="/auth", dependencies=[Depends(require_jrc_auth_policy)])
    for name in ("reset-password", "forgot-password", "future-feature"):
        router.add_api_route(f"/{name}", lambda: {"ok": True}, methods=["POST", "GET"])
    for path in JRC_ALLOWED_AUTH_ROUTES:
        router.add_api_route(path.removeprefix("/auth"), lambda: {"ok": True}, methods=["GET"])
    app = FastAPI()
    app.include_router(router)
    api = TestClient(app)
    for name in ("reset-password", "forgot-password", "future-feature"):
        for method in ("get", "post"):
            response = getattr(api, method)(f"/auth/{name}", headers={"X-Real-IP": "127.0.0.1"})
            assert (response.status_code, response.json()) == (403, {"detail": "Sign in through JR Content."})
    for path in JRC_ALLOWED_AUTH_ROUTES:
        assert api.get(path).status_code == 200


def test_allow_list_matches_real_router_routes():
    """Every route of the real /auth router is either allowed on purpose or refused."""
    from apps.api.routers.auth import JRC_ALLOWED_AUTH_ROUTES, router

    paths = {route.path for route in router.routes}
    assert JRC_ALLOWED_AUTH_ROUTES <= paths
    assert paths - JRC_ALLOWED_AUTH_ROUTES == {
        "/auth/send-magic-code", "/auth/verify-magic-code", "/auth/set-password", "/auth/invite/{token}",
        "/auth/accept-invite", "/auth/login", "/auth/change-password",
    }


def test_standalone_mode_keeps_local_auth(client, mock_db, monkeypatch):
    monkeypatch.setattr(settings, "jrc_mode", "")
    mock_db.first.return_value = None
    response = client.post("/auth/send-magic-code", json={"email": "someone@example.com"})
    assert response.status_code != 403
