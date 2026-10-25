"""Redis outages must refuse operations that rely on its abuse controls."""

from apps.api.services import redis_service


def _redis_down(*args, **kwargs):
    raise ConnectionError("redis unavailable")


def test_sensitive_rate_limit_fails_closed_when_redis_is_down(monkeypatch):
    monkeypatch.setattr(redis_service, "get_redis", _redis_down)

    assert redis_service.check_rate_limit("127.0.0.1", "login", 10, 60, fail_closed=True) == (False, -1)
    assert redis_service.check_rate_limit("127.0.0.1", "ordinary", 10, 60) == (True, 0)


def test_login_returns_service_unavailable_when_redis_is_down(client, monkeypatch):
    monkeypatch.setattr(redis_service, "get_redis", _redis_down)

    response = client.post("/auth/login", json={"email": "x@example.invalid", "password": "test"})

    assert response.status_code == 503


def test_upload_start_returns_service_unavailable_when_redis_is_down(
    client, auth_headers, monkeypatch
):
    monkeypatch.setattr(redis_service, "get_redis", _redis_down)

    response = client.post(
        "/upload/initiate",
        json={
            "project_id": "00000000-0000-4000-8000-000000000001",
            "asset_name": "test",
            "original_filename": "test.mp4",
            "mime_type": "video/mp4",
            "file_size_bytes": 100,
        },
        headers=auth_headers,
    )

    assert response.status_code == 503
