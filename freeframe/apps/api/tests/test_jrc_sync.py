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


@pytest.mark.parametrize("email", [
    "no-at-sign", "@example.com", "person@", "pers on@example.com", "a\n@b.cz", "a\t@b.cz",
    "a@b@c.cz", "a,b@c.cz", "a;b@c.cz", "<a@b.cz>", '"a"@b.cz',
])
def test_account_rejects_malformed_email(client, sync_auth, mock_db, email):
    response = client.put(f"/jrc-sync/accounts/{uuid.uuid4()}", json={
        "email": email, "name": "Person", "is_active": True,
    }, headers=sync_auth)
    assert response.status_code == 422
    mock_db.commit.assert_not_called()


def test_account_email_is_lowercased(client, sync_auth, mock_db):
    response = client.put(f"/jrc-sync/accounts/{uuid.uuid4()}", json={
        "email": "Ella@Example.INVALID", "name": "Ella", "is_active": True,
    }, headers=sync_auth)
    assert response.status_code == 200
    assert mock_db.add.call_args.args[0].email == "ella@example.invalid"


def test_email_conflict_is_case_insensitive(client, sync_auth, mock_db):
    # First lookup: managed identity does not exist; second: another account owns the e-mail.
    mock_db.first.side_effect = [None, MagicMock()]
    response = client.put(f"/jrc-sync/accounts/{uuid.uuid4()}", json={
        "email": "PERSON@example.com", "name": "Person", "is_active": True,
    }, headers=sync_auth)
    assert response.status_code == 409
    mock_db.add.assert_not_called()
    mock_db.commit.assert_not_called()


def test_service_account_email_match_ignores_case(client, auth_headers, test_user, mock_db, monkeypatch):
    test_user.is_superadmin = True
    monkeypatch.setenv("JRC_FF_SYNC_EMAIL", test_user.email.upper())
    response = client.put(f"/jrc-sync/projects/{uuid.uuid4()}", json={"name": "Project"}, headers=auth_headers)
    assert response.status_code == 200


def test_deactivate_missing_account_does_not_create_it(client, sync_auth, mock_db):
    source_id = uuid.uuid4()
    response = client.post(f"/jrc-sync/accounts/{source_id}/deactivate", headers=sync_auth)
    assert response.status_code == 200
    assert response.json() == {"id": str(remote_id("account", source_id)), "found": False}
    mock_db.add.assert_not_called()
    mock_db.commit.assert_not_called()


def test_deactivate_existing_account(client, sync_auth, mock_db):
    from apps.api.models.user import UserStatus

    managed = MagicMock(deleted_at=None, is_superadmin=False, status=UserStatus.active)
    mock_db.first.return_value = managed
    response = client.post(f"/jrc-sync/accounts/{uuid.uuid4()}/deactivate", headers=sync_auth)
    assert response.status_code == 200 and response.json()["found"] is True
    assert managed.status == UserStatus.deactivated
    mock_db.commit.assert_called_once()


def test_deactivate_refuses_privileged_account(client, sync_auth, mock_db):
    mock_db.first.return_value = MagicMock(deleted_at=None, is_superadmin=True)
    response = client.post(f"/jrc-sync/accounts/{uuid.uuid4()}/deactivate", headers=sync_auth)
    assert response.status_code == 409
    mock_db.commit.assert_not_called()


def test_deactivate_rejects_regular_user(client, auth_headers, mock_db):
    response = client.post(f"/jrc-sync/accounts/{uuid.uuid4()}/deactivate", headers=auth_headers)
    assert response.status_code == 403
    mock_db.commit.assert_not_called()
