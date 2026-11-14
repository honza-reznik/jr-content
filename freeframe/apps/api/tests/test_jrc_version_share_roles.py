"""P2-B19 (N-K20-p026rg1-06): a client (reviewer) and a viewer cannot add a version or create a share link.

The web player hides both buttons for them; this pins that the API refuses the same actions
(real Postgres rows, real permission checks), so hiding is never the only guard.
"""
import uuid

import pytest

from apps.api.models.user import UserStatus
from apps.api.services.auth_service import create_access_token


@pytest.mark.parametrize("role", ["reviewer", "viewer"])
def test_reviewer_and_viewer_cannot_upload_a_version_or_create_a_share_link(client, real_db, role):
    from apps.api.database import get_db
    from apps.api.main import app
    from apps.api.models.asset import Asset, AssetType
    from apps.api.models.project import Project, ProjectMember, ProjectRole
    from apps.api.models.user import User

    user = User(id=uuid.uuid4(), email=f"{uuid.uuid4().hex}@example.com", name="Client",
                jrc_managed=True, token_version=1, status=UserStatus.active)
    real_db.add(user)
    real_db.flush()
    project = Project(id=uuid.uuid4(), name="Client project", created_by=user.id)
    real_db.add(project)
    real_db.flush()
    real_db.add(ProjectMember(project_id=project.id, user_id=user.id, role=ProjectRole(role)))
    asset = Asset(id=uuid.uuid4(), project_id=project.id, created_by=user.id, name="Video", asset_type=AssetType.video)
    real_db.add(asset)
    real_db.flush()
    app.dependency_overrides[get_db] = lambda: real_db
    headers = {"Authorization": f"Bearer {create_access_token(str(user.id), token_version=1)}"}
    try:
        upload = client.post("/upload/initiate", headers=headers, json={
            "project_id": str(project.id), "asset_id": str(asset.id), "asset_name": "Video",
            "original_filename": "v2.mp4", "mime_type": "video/mp4", "file_size_bytes": 1024})
        assert upload.status_code == 403
        share = client.post(f"/assets/{asset.id}/share", headers=headers, json={})
        assert share.status_code == 403
        assert client.get(f"/assets/{asset.id}/shares", headers=headers).status_code == 403
    finally:
        app.dependency_overrides.pop(get_db, None)
