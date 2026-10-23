import uuid
from unittest.mock import MagicMock

import pytest
from sqlalchemy.exc import IntegrityError

from apps.api.routers.jrc_sync import NAMESPACE, remote_id


@pytest.fixture
def sync_auth(auth_headers, test_user, monkeypatch):
    monkeypatch.setenv("JRC_FF_SYNC_EMAIL", test_user.email)
    test_user.is_superadmin = True
    return auth_headers


def test_sync_rejects_regular_user(client, auth_headers):
    response = client.put(f"/jrc-sync/accounts/{uuid.uuid4()}", json={
        "email": "person@example.com", "name": "Person", "is_active": True,
    }, headers=auth_headers)
    assert response.status_code == 403


def test_sync_rejects_other_admin(client, auth_headers, test_user, monkeypatch):
    test_user.is_superadmin = True
    monkeypatch.setenv("JRC_FF_SYNC_EMAIL", "other@example.com")
    response = client.put(f"/jrc-sync/projects/{uuid.uuid4()}", json={"name": "Project"}, headers=auth_headers)
    assert response.status_code == 403


def test_account_has_stable_id_no_password_or_invite(client, sync_auth, mock_db):
    source_id = uuid.uuid4()
    response = client.put(f"/jrc-sync/accounts/{source_id}", json={
        "email": "person@example.com", "name": "Person", "is_active": True,
    }, headers=sync_auth)
    assert response.status_code == 200
    user = mock_db.add.call_args.args[0]
    assert str(user.id) == response.json()["id"] == str(uuid.uuid5(NAMESPACE, f"account:{source_id}"))
    assert user.password_hash is None
    assert user.invite_token is None
    assert user.is_superadmin is False


def test_conflicting_email_cannot_take_over_identity(client, sync_auth, mock_db):
    mock_db.commit.side_effect = IntegrityError("sql", {}, Exception())
    response = client.put(f"/jrc-sync/accounts/{uuid.uuid4()}", json={
        "email": "person@example.com", "name": "Person", "is_active": True,
    }, headers=sync_auth)
    assert response.status_code == 409
    mock_db.rollback.assert_called_once()


@pytest.mark.parametrize("deleted, admin", [(True, False), (False, True)])
def test_deleted_or_privileged_account_is_not_overwritten(client, sync_auth, mock_db, deleted, admin):
    mock_db.first.return_value = MagicMock(deleted_at=object() if deleted else None, is_superadmin=admin)
    response = client.put(f"/jrc-sync/accounts/{uuid.uuid4()}", json={
        "email": "person@example.com", "name": "Person", "is_active": True,
    }, headers=sync_auth)
    assert response.status_code == 409
    mock_db.commit.assert_not_called()


def test_project_private_and_owned_by_service(client, sync_auth, mock_db, test_user):
    source_id = uuid.uuid4()
    response = client.put(f"/jrc-sync/projects/{source_id}", json={"name": "Project"}, headers=sync_auth)
    assert response.status_code == 200
    project, owner = [call.args[0] for call in mock_db.add.call_args_list]
    assert project.id == remote_id("project", source_id)
    assert project.is_public is False
    assert project.created_by == owner.user_id == test_user.id
    assert owner.role.value == "owner"
    assert owner.deleted_at is None


def test_deleted_project_is_not_restored(client, sync_auth, mock_db):
    mock_db.first.return_value = MagicMock(deleted_at=object())
    response = client.put(f"/jrc-sync/projects/{uuid.uuid4()}", json={"name": "Project"}, headers=sync_auth)
    assert response.status_code == 409


@pytest.mark.parametrize("email", ["ella-demo@example.invalid", "client@studio.local", "person@example.test"])
def test_account_accepts_local_demo_domains(client, sync_auth, mock_db, email):
    response = client.put(f"/jrc-sync/accounts/{uuid.uuid4()}", json={
        "email": email, "name": "Demo", "is_active": True,
    }, headers=sync_auth)
    assert response.status_code == 200
    assert mock_db.add.call_args.args[0].email == email


@pytest.mark.parametrize("email", ["no-at-sign", "@example.com", "person@", "pers on@example.com", "a\n@b.cz"])
def test_account_rejects_malformed_email(client, sync_auth, mock_db, email):
    response = client.put(f"/jrc-sync/accounts/{uuid.uuid4()}", json={
        "email": email, "name": "Person", "is_active": True,
    }, headers=sync_auth)
    assert response.status_code == 422
    mock_db.commit.assert_not_called()
