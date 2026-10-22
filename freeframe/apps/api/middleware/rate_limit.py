"""
IP-based rate limiting dependency for FastAPI routes.

Usage:
    @router.post("/endpoint", dependencies=[Depends(rate_limit("action", 5, 60))])
    def my_endpoint(): ...

This limits to 5 requests per 60 seconds per IP for "action".
"""

from fastapi import HTTPException, Request, status
from ..services.redis_service import check_rate_limit


def rate_limit(action: str, max_requests: int, window_seconds: int):
    """
    Returns a FastAPI dependency that enforces IP-based rate limiting.

    Args:
        action: Unique key for this rate limit (e.g. "send_magic_code")
        max_requests: Maximum requests allowed in the window
        window_seconds: Time window in seconds
    """

    def _dependency(request: Request):
        # Use X-Real-Ip (set by trusted reverse proxy like Traefik) or fall back to ASGI client IP
        # Do NOT use X-Forwarded-For as it can be spoofed by the client
        ip = request.headers.get("x-real-ip") or (request.client.host if request.client else "unknown")

        allowed, retry_after = check_rate_limit(ip, action, max_requests, window_seconds)
        if not allowed:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Too many requests. Please try again in {retry_after} seconds.",
                headers={"Retry-After": str(retry_after)},
            )

    return _dependency
