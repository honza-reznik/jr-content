"""P2-B05 R2 live check through the real proxy, BB gateway and FF gateway (own project jrc-p2b05 only).

slow:  a 120 MB original read at a limited rate takes longer than the old 300 s total limit and the
       369 s capacity lease; it must arrive whole (SHA-256), with the lease still counted while it runs.
stall: a client that stops reading must be cut by the idle limit, not served for ever.
Run in Docker on the host network: python3 scripts/P2-B05/A-slow.py <slow|stall>
"""

import hashlib
import http.client
import json
import os
import socket
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

assert os.environ.get("JRC_PROJECT") == "jrc-p2b05"
OUT = Path("ops/test-output/P2-B05/A")
cookie = (OUT / "owner-cookie.txt").read_text().strip()
versions = {v["number"]: v for v in json.loads((OUT / "versions.json").read_text())}
expected = hashlib.sha256((OUT / "Velký originál.MOV").read_bytes()).hexdigest()
size = (OUT / "Velký originál.MOV").stat().st_size


def gateway_address():
    connection = http.client.HTTPConnection("127.0.0.1", 13905, timeout=30)
    connection.request("GET", versions[2]["download_url"], headers={"Cookie": cookie})
    response = connection.getresponse()
    assert response.status == 302, response.status
    location = response.getheader("Location")
    response.read()
    connection.close()
    parts = urlsplit(location)
    return parts.path + ("?" + parts.query if parts.query else "")




def slow(rate):
    path = gateway_address()
    connection = http.client.HTTPConnection("127.0.0.1", 13905, timeout=60)
    connection.request("GET", path, headers={"Cookie": cookie})
    response = connection.getresponse()
    assert response.status == 200, response.status
    length = int(response.getheader("Content-Length"))
    digest, received, started, marks = hashlib.sha256(), 0, time.monotonic(), []
    while True:
        piece = response.read(64 * 1024)
        if not piece:
            break
        digest.update(piece)
        received += len(piece)
        elapsed = time.monotonic() - started
        ahead = received / rate - elapsed
        if ahead > 0:
            time.sleep(ahead)
        if not marks or elapsed - marks[-1][0] >= 30:
            marks.append((round(elapsed), received))
            print(f"progress {round(elapsed)} s {received} B", flush=True)
    elapsed = time.monotonic() - started
    result = {
        "disposition": response.getheader("Content-Disposition"),
        "content_length": length,
        "received": received,
        "seconds": round(elapsed, 1),
        "sha256_ok": digest.hexdigest() == expected,
        "rate_bytes_per_s": rate,
    }
    print(json.dumps(result), flush=True)
    assert received == size == length and result["sha256_ok"], result
    assert elapsed > 369, "the transfer must outlast the old 300 s limit and the 369 s lease"


def stall(pause):
    path = gateway_address()
    sock = socket.create_connection(("127.0.0.1", 13905), timeout=120)
    # A small receive window, so the stall reaches the servers quickly.
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 64 * 1024)
    sock.sendall(f"GET {path} HTTP/1.1\r\nHost: 127.0.0.1:13905\r\nCookie: {cookie}\r\nConnection: close\r\n\r\n".encode())
    received = 0
    while received < 2 * 1024 * 1024:
        received += len(sock.recv(64 * 1024))
    print(f"read {received} B, now not reading for {pause} s", flush=True)
    time.sleep(pause)
    started = time.monotonic()
    while True:
        try:
            piece = sock.recv(256 * 1024)
        except (ConnectionResetError, TimeoutError) as exc:
            print(f"connection ended: {type(exc).__name__}", flush=True)
            break
        if not piece:
            print("connection closed by the server", flush=True)
            break
        received += len(piece)
    result = {"received_with_headers": received, "file_size": size, "drain_seconds": round(time.monotonic() - started, 1)}
    print(json.dumps(result), flush=True)
    assert received < size, "a stalled client must not get the whole file"


if __name__ == "__main__":
    if sys.argv[1] == "slow":
        slow(int(sys.argv[2]) if len(sys.argv) > 2 else 290 * 1024)
    else:
        stall(int(sys.argv[2]) if len(sys.argv) > 2 else 90)
