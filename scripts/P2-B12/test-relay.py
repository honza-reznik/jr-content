"""Local protocol and wall-clock tests, run in the credential-free relay container."""
import http.client
import importlib.util
import subprocess
import threading
import time
from http.server import HTTPServer
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("relay", "/relay/channel-favicon-relay.py")
relay = importlib.util.module_from_spec(spec)
spec.loader.exec_module(relay)
server = HTTPServer(("127.0.0.1", 0), relay.Handler)
thread = threading.Thread(target=server.serve_forever, daemon=True)
thread.start()


def request(path, body):
    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=12)
    try:
        connection.request("POST", path, body=body)
        response = connection.getresponse()
        return response.status, response.read()
    finally:
        connection.close()


try:
    with patch.object(relay.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, b"png")) as child:
        assert request("/favicon", b"https://example.invalid/") == (200, b"png")
        assert child.call_args.kwargs["timeout"] == 8
        child.reset_mock()
        assert request("/arbitrary", b"https://example.invalid/")[0] == 422
        assert request("/favicon", b"x" * 2001)[0] == 422
        child.assert_not_called()
    # Run a genuinely hanging child through the same subprocess timeout, with
    # a short deadline for this local test. No external DNS or HTTP is used.
    original = subprocess.run

    def stalled(*args, **kwargs):
        return original([relay.sys.executable, "-c", "import time; time.sleep(30)"], timeout=.15, stdout=subprocess.PIPE)

    started = time.monotonic()
    with patch.object(relay.subprocess, "run", side_effect=stalled):
        assert request("/favicon", b"https://example.invalid/")[0] == 422
    assert time.monotonic() - started < 2
    print("Relay: protocol, size limits, timeout and child termination OK.")
finally:
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)
