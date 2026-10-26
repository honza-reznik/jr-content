"""Instance-wide Redis budgets, shared by all API processes."""
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from starlette._utils import get_route_path
from ..services.redis_service import check_rate_limit

READ_LIMIT = 600
WRITE_LIMIT = 300
WINDOW_SECONDS = 60
EXEMPT_PATHS = {"/health", "/docs", "/redoc", "/openapi.json"}


class GlobalRateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        path = get_route_path(request.scope)
        if path in EXEMPT_PATHS or path.startswith("/stream/"):
            return await call_next(request)
        write = request.method in ("POST", "PUT", "PATCH", "DELETE")
        allowed, retry = check_rate_limit(
            "total", "global_w" if write else "global_r",
            (WRITE_LIMIT if write else READ_LIMIT) * 100, WINDOW_SECONDS, fail_closed=True,
        )
        if not allowed:
            return JSONResponse(status_code=503 if retry < 0 else 429,
                                content={"detail": "Temporarily unavailable" if retry < 0 else "Too many requests"},
                                headers={"Retry-After": str(5 if retry < 0 else retry)})
        return await call_next(request)
