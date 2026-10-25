"""Bounded, thread-safe HLS admission budgets for one API process."""
from collections import Counter
from threading import Lock
from time import monotonic

from fastapi import HTTPException

from ..config import settings


class HlsLimits:
    def __init__(self):
        self.lock = Lock()
        self.requests = {}
        self.active = Counter()
        self.total = 0

    def acquire(self, ip: str, session: str):
        keys = (("ip", ip), ("session", session))
        with self.lock:
            now = monotonic()
            self.requests = {key: value for key, value in self.requests.items() if value[0] > now}
            rates = (settings.hls_requests_per_minute_ip, settings.hls_requests_per_minute_session)
            slots = (settings.hls_concurrent_streams_ip, settings.hls_concurrent_streams_session)
            if (self.total >= settings.hls_concurrent_streams_total
                    or len(self.requests) >= 4096
                    or any(self.active[key] >= limit for key, limit in zip(keys, slots))
                    or any(self.requests.get(key, (0, 0))[1] >= limit for key, limit in zip(keys, rates))):
                raise HTTPException(429, "Too many streaming requests", headers={"Retry-After": "60"})
            for key in keys:
                expiry, count = self.requests.get(key, (now + 60, 0))
                self.requests[key] = (expiry, count + 1)
                self.active[key] += 1
            self.total += 1

        released = False

        def release():
            nonlocal released
            with self.lock:
                if released:
                    return
                released = True
                self.total -= 1
                for key in keys:
                    self.active[key] -= 1
                    if not self.active[key]:
                        del self.active[key]

        return release


hls_limits = HlsLimits()
