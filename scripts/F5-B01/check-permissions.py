"""Ověření skutečných oprávnění API; tokeny vznikají pouze v kontejneru FreeFrame."""

import sys
import uuid
import httpx

from apps.api.routers.jrc_sync import remote_id
from apps.api.services.auth_service import create_access_token

workspace_id = uuid.UUID("f5b01000-0000-4000-8000-000000000001")
project_id = remote_id("project", workspace_id)
stage = sys.argv[1]


def request(number, method, path, body=None):
    user_id = remote_id("account", uuid.UUID(f"f5b01000-0000-4000-8000-{number:012}"))
    # Test fixture only: no password or JWT secret leaves the FreeFrame container.
    token = create_access_token(str(user_id))
    return httpx.request(method, f"http://127.0.0.1:8000{path}", json=body,
                         headers={"Authorization": f"Bearer {token}"}, timeout=15)


if stage == "initial":
    for number, role in [(101, "editor"), (102, "reviewer"), (103, "viewer")]:
        response = request(number, "GET", f"/projects/{project_id}")
        assert response.status_code == 200, (number, response.status_code)
        assert response.json()["role"] == role
        assert not response.json()["is_public"]
    assert request(104, "GET", f"/projects/{project_id}").status_code == 403
    for number in [102, 103]:
        assert request(number, "POST", "/upload/initiate", {
            "project_id": str(project_id), "asset_name": "Forbidden upload", "original_filename": "test.mp4",
            "mime_type": "video/mp4", "file_size_bytes": 128,
        }).status_code == 403
        assert request(number, "PATCH", f"/projects/{project_id}", {"name": "Forbidden rename"}).status_code == 403
        assert request(number, "PUT", f"/jrc-sync/accounts/{uuid.uuid4()}", {
            "email": "forbidden@example.com", "name": "Forbidden", "is_active": True,
        }).status_code == 403
elif stage == "changed":
    response = request(102, "GET", f"/projects/{project_id}")
    assert response.status_code == 200 and response.json()["role"] == "viewer"
    assert request(101, "GET", f"/projects/{project_id}").status_code == 401
    assert request(103, "GET", f"/projects/{project_id}").status_code == 403
elif stage in {"archived", "deleted"}:
    for number in [101, 102, 103, 104]:
        assert request(number, "GET", f"/projects/{project_id}").status_code == 403
else:
    raise AssertionError(stage)
print(f"OK FreeFrame live permissions: {stage}")
