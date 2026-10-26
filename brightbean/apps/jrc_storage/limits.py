"""The same Redis DB, namespace, actor IDs and leases as FreeFrame HLS."""

import hashlib
import uuid
from contextlib import suppress
from functools import lru_cache

import redis
from django.conf import settings

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


class TransferUnavailableError(Exception):
    def __init__(self, status):
        self.status = status


@lru_cache(maxsize=1)
def connection():
    return redis.Redis.from_url(
        getattr(settings, "JRC_FILES_REDIS_URL", "redis://jrc-redis:6379/0"),
        socket_connect_timeout=1,
        socket_timeout=1,
    )


def acquire(user):
    identity = uuid.uuid5(uuid.UUID("29c4ee9e-ab19-48c4-bb44-2fc77109b234"), f"account:{user.pk}")
    digest = hashlib.sha256(f"user:{identity}".encode()).hexdigest()
    keys = ["hls:active:total", f"hls:active:{digest}", "hls:rate:total", f"hls:rate:{digest}"]
    lease = uuid.uuid4().hex
    try:
        admitted = connection().eval(ACQUIRE, 4, *keys, 32, 4, 6000, 240, 369, lease)
    except redis.RedisError:
        raise TransferUnavailableError(503) from None
    if not admitted:
        raise TransferUnavailableError(429)

    def release():
        with suppress(redis.RedisError):
            connection().eval(RELEASE, 2, *keys[:2], lease)

    return release
