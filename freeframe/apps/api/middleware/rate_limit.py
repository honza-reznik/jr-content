"""Shared admission budgets, keyed by verified users or normalized accounts."""
import hashlib

from fastapi import Depends, HTTPException, Request
from .auth import get_current_user, get_optional_user
from .trusted_proxy import client_ip
from ..services.redis_service import check_rate_limit


def enforce_limit(identity, action, maximum, window, *, fail_closed=True):
    allowed, retry = check_rate_limit(identity, action, maximum, window, fail_closed=fail_closed)
    if retry < 0:
        raise HTTPException(503, "Temporarily unavailable. Please retry.", headers={"Retry-After": "5"})
    if not allowed:
        raise HTTPException(429, "Too many requests. Please try again later.", headers={"Retry-After": str(retry)})


def enforce_user_limit(user, action, maximum, window, *, fail_closed=True):
    enforce_limit(f"user:{user.id}", action, maximum, window, fail_closed=fail_closed)
    enforce_limit("total", action, maximum * 100, window, fail_closed=fail_closed)


def rate_limit(action: str, max_requests: int, window_seconds: int, *, fail_closed: bool = False):
    # Preserve guest abuse controls; verified users never share the IP budget.
    def dependency(request: Request, user=Depends(get_optional_user)):
        identity = f"user:{user.id}" if user else f"ip:{client_ip(request)}"
        enforce_limit(identity, action, max_requests, window_seconds, fail_closed=fail_closed)
        enforce_limit("total", action, max_requests * 100, window_seconds, fail_closed=fail_closed)
    return dependency


def user_rate_limit(action: str, maximum: int, window: int, *, fail_closed: bool = True):
    def dependency(user=Depends(get_current_user)):
        enforce_user_limit(user, action, maximum, window, fail_closed=fail_closed)
    return dependency


def account_rate_limit(action: str, maximum: int, window: int, schema):
    def dependency(body: schema):
        # Use the endpoint's validated EmailStr, including display-name and
        # international-domain normalization, before deriving the budget key.
        email = str(body.email).casefold()
        identity = hashlib.sha256(email.encode()).hexdigest()
        enforce_limit(f"account:{identity}", action, maximum, window)
        enforce_limit("total", action, maximum * 100, window)
    return dependency
