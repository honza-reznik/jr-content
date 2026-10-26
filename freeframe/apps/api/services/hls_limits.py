"""Atomic Redis admission with expiring, individually owned streaming leases."""
import hashlib
import uuid

from fastapi import HTTPException

from ..config import settings
from .redis_service import get_redis

# Redis supplies the clock so worker clocks cannot disagree. A crashed worker's
# lease expires after the hard response deadline plus bounded storage setup.
ACQUIRE = """
local time = redis.call('TIME')
local now = tonumber(time[1]) + tonumber(time[2]) / 1000000
for i = 1, 2 do
    redis.call('ZREMRANGEBYSCORE', KEYS[i], '-inf', now)
    if redis.call('ZCARD', KEYS[i]) >= tonumber(ARGV[i]) then return 0 end
end
for i = 3, 4 do
    if tonumber(redis.call('GET', KEYS[i]) or '0') >= tonumber(ARGV[i]) then return 0 end
end
for i = 1, 2 do
    redis.call('ZADD', KEYS[i], now + tonumber(ARGV[5]), ARGV[6])
    redis.call('EXPIRE', KEYS[i], ARGV[5])
end
for i = 3, 4 do
    if redis.call('INCR', KEYS[i]) == 1 then redis.call('EXPIRE', KEYS[i], 60) end
end
return 1
"""
RELEASE = """
for i = 1, #KEYS do redis.call('ZREM', KEYS[i], ARGV[1]) end
return 1
"""


class HlsLimits:
    def __init__(self, namespace="hls"):
        self.namespace = namespace

    def acquire(self, identity: str):
        digest = hashlib.sha256(identity.encode()).hexdigest()
        keys = [f"{self.namespace}:active:total", f"{self.namespace}:active:{digest}",
                f"{self.namespace}:rate:total", f"{self.namespace}:rate:{digest}"]
        lease = uuid.uuid4().hex
        ttl = (settings.hls_stream_timeout_seconds + settings.hls_s3_connect_timeout_seconds
               + settings.hls_s3_read_timeout_seconds + 60)
        try:
            admitted = get_redis().eval(
                ACQUIRE, 4, *keys, settings.hls_concurrent_streams_total,
                settings.hls_concurrent_streams_user, settings.hls_requests_per_minute_total,
                settings.hls_requests_per_minute_user, int(ttl) + 1, lease,
            )
        except Exception:
            raise HTTPException(503, "Streaming temporarily unavailable", headers={"Retry-After": "5"}) from None
        if not admitted:
            raise HTTPException(429, "Too many streaming requests", headers={"Retry-After": "60"})

        def release():
            # Removing this UUID is idempotent and cannot release another lease,
            # including after expiry/reacquisition. On Redis failure TTL recovers it.
            try:
                get_redis().eval(RELEASE, 2, *keys[:2], lease)
            except Exception:
                pass
        return release


hls_limits = HlsLimits()
