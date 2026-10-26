"""Živá regrese produkční cache: odmítnuté spojení i Redis, který neodpovídá.

Spouštět pouze uvnitř vlastního jrc-bb-web přes manage.py shell. Místní TCP
server přijímá spojení, ale nic nevrací; skutečná Redis cache musí včas skončit.
"""
import socket
import threading
import time

from django.conf import settings
from django.test import Client, RequestFactory, override_settings

from apps.accounts.middleware import AuthRateLimitMiddleware


cache_config = settings.CACHES["default"]
assert cache_config["BACKEND"] == "django.core.cache.backends.redis.RedisCache"
options = cache_config["OPTIONS"]
assert 0 < options["socket_connect_timeout"] <= 1
assert 0 < options["socket_timeout"] <= 1
server = socket.socket()
server.bind(("127.0.0.1", 0))
port = server.getsockname()[1]
server.listen()
server.settimeout(0.1)
stop = threading.Event()
connections = []


def accept_without_reply():
    while not stop.is_set():
        try:
            connection, _ = server.accept()
            connections.append(connection)
        except socket.timeout:
            continue


thread = threading.Thread(target=accept_without_reply)
thread.start()
try:
    for failure in ("silent", "refused"):
        if failure == "refused":
            stop.set()
            thread.join(timeout=2)
            server.close()
            for connection in connections:
                connection.close()
        config = {**cache_config, "LOCATION": f"redis://127.0.0.1:{port}/1"}
        with override_settings(CACHES={"default": config}):
            def forbidden_view(request):
                raise AssertionError("Při výpadku ochrany se autentizace nesmí provést")

            start = time.monotonic()
            response = AuthRateLimitMiddleware(forbidden_view)(
                RequestFactory().post("/accounts/login/", {"login": "test@example.invalid"})
            )
            elapsed = time.monotonic() - start
            assert response.status_code == 503, response.status_code
            assert elapsed < 3, elapsed
            print(f"OK Redis {failure}: login 503 za {elapsed:.2f} s")
            for path in ("/api/v1/me/", "/api/v1/mcp/"):
                client = Client(HTTP_HOST="localhost", HTTP_AUTHORIZATION="Bearer invalid")
                response = (client.post(path, data={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                                        content_type="application/json", secure=True)
                            if path.endswith("/mcp/") else client.get(path, secure=True))
                assert response.status_code == 503, (path, response.status_code)
                print(f"OK Redis {failure}: {path} 503")
finally:
    stop.set()
    thread.join(timeout=2)
    server.close()
    for connection in connections:
        connection.close()
