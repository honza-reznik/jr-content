"""Upgrade also closes alternate login on identities created before F5-B04."""

import importlib.util
from pathlib import Path
import uuid

from sqlalchemy import text

from apps.api.models.user import User, UserStatus


def test_existing_managed_identity_loses_password_invite_and_old_sessions(real_db, monkeypatch):
    legacy = User(
        id=uuid.uuid5(uuid.NAMESPACE_URL, uuid.uuid4().hex), email=f"{uuid.uuid4().hex}@example.com",
        name="Legacy managed", jrc_managed=False, password_hash="legacy-hash", invite_token="legacy-invite",
        token_version=7, status=UserStatus.active,
    )
    local = User(id=uuid.uuid4(), email=f"{uuid.uuid4().hex}@example.com", name="Local admin",
                 is_superadmin=True, password_hash="local-hash", token_version=3)
    real_db.add_all([legacy, local])
    real_db.flush()
    path = Path(__file__).resolve().parents[1] / "alembic/versions/jrc_f5b04_managed_accounts.py"
    spec = importlib.util.spec_from_file_location("jrc_managed_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    # The test database already has the column. Exercise the migration's real
    # backfill SQL inside the fixture transaction, which is rolled back.
    monkeypatch.setattr(migration.op, "add_column", lambda *args, **kwargs: None)
    monkeypatch.setattr(migration.op, "execute", lambda sql: real_db.execute(text(sql)))
    migration.upgrade()
    real_db.expire_all()
    assert legacy.jrc_managed is True
    assert legacy.password_hash is None and legacy.invite_token is None
    assert legacy.token_version == 8
    assert local.jrc_managed is False
    assert local.password_hash == "local-hash" and local.token_version == 3
