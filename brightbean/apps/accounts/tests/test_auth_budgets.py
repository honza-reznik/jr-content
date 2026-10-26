from unittest.mock import Mock

import pytest
from django.core.cache import cache
from django.http import HttpResponse
from django.test import RequestFactory
from redis.exceptions import ConnectionError, TimeoutError

from apps.accounts import middleware


def test_normalized_accounts_do_not_share_ip_budget():
    cache.clear()
    handler = middleware.AuthRateLimitMiddleware(lambda request: HttpResponse("ok"))
    factory = RequestFactory()

    def attempt(email, ip="127.0.0.1"):
        return handler(factory.post("/accounts/login/", {"login": email}, REMOTE_ADDR=ip)).status_code

    try:
        for _ in range(middleware.AUTH_RATE_LIMIT):
            assert attempt("one@example.invalid") == 200
        assert attempt(" ONE@EXAMPLE.INVALID ", "192.0.2.1") == 429
        assert attempt("two@example.invalid") == 200
        cache.set("auth_ratelimit:total", middleware.AUTH_TOTAL_RATE_LIMIT, 60)
        assert attempt("three@example.invalid") == 429
    finally:
        cache.clear()


@pytest.mark.parametrize("failure", [ConnectionError, TimeoutError])
@pytest.mark.parametrize("operation", ["add", "incr"])
def test_auth_cache_outage_refuses_request_without_running_view(monkeypatch, failure, operation):
    view = Mock(return_value=HttpResponse("ok"))
    monkeypatch.setattr(middleware.cache, "add", Mock(return_value=False))
    monkeypatch.setattr(middleware.cache, operation, Mock(side_effect=failure("unavailable")))
    handler = middleware.AuthRateLimitMiddleware(view)
    response = handler(RequestFactory().post("/accounts/login/", {"login": "victim@example.com"}))
    assert response.status_code == 503
    assert response["Retry-After"] == "5"
    view.assert_not_called()


@pytest.mark.parametrize("failure", [ConnectionError, TimeoutError])
def test_view_cache_outage_is_handled_by_exception_middleware(failure):
    handler = middleware.AuthRateLimitMiddleware(lambda request: HttpResponse("ok"))
    response = handler.process_exception(RequestFactory().post("/members/invite/"), failure("unavailable"))
    assert response.status_code == 503
    assert handler.process_exception(RequestFactory().get("/"), ValueError("unrelated")) is None
