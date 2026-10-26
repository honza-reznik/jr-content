"""Middleware exemptions must still work behind a stripped public API prefix."""
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from apps.api.middleware import setup_guard
from apps.api.middleware.global_rate_limit import GlobalRateLimitMiddleware


@pytest.mark.parametrize("prefix", ["", "/video-api"])
@pytest.mark.parametrize("path", ["/health", "/setup/status", "/share/local-test"])
def test_unfinished_setup_exemptions_with_root_path(monkeypatch, prefix, path):
    monkeypatch.setattr(setup_guard, "_setup_complete", False)
    db = MagicMock()
    db.return_value.query.return_value.filter.return_value.first.return_value = None
    monkeypatch.setattr("apps.api.database.SessionLocal", db)
    app = FastAPI(root_path=prefix)
    app.add_middleware(setup_guard.SetupGuardMiddleware)
    app.add_api_route(path, lambda: {"ok": True})
    app.add_api_route("/projects", lambda: {"private": True})
    with TestClient(app) as client:
        assert client.get(prefix + path).status_code == 200
        db.assert_not_called()
        assert client.get(prefix + "/projects").status_code == 503


@pytest.mark.parametrize("prefix", ["", "/video-api"])
@pytest.mark.parametrize("path", ["/health", "/stream/hls/master.m3u8"])
def test_rate_limit_exemptions_with_root_path(monkeypatch, prefix, path):
    check = MagicMock(return_value=(False, 60))
    monkeypatch.setattr("apps.api.middleware.global_rate_limit.check_rate_limit", check)
    app = FastAPI(root_path=prefix)
    app.add_middleware(GlobalRateLimitMiddleware)
    app.add_api_route(path, lambda: {"ok": True})
    app.add_api_route("/projects", lambda: {"ok": True})
    with TestClient(app) as client:
        assert client.get(prefix + path).status_code == 200
        check.assert_not_called()
        assert client.get(prefix + "/projects").status_code == 429
