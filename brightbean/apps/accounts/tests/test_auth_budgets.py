from django.core.cache import cache
from django.http import HttpResponse
from django.test import RequestFactory

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
