"""Only fetch public favicons. No database, storage or SMTP credentials."""

import subprocess
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

# The mounted directory contains only the favicon downloader, not the app.
sys.path.insert(0, "/favicon")


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        self.connection.settimeout(3)
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if self.path != "/favicon" or not 0 < length <= 2000:
                raise ValueError
            url = self.rfile.read(length).decode("utf-8")
            result = subprocess.run(
                [sys.executable, __file__, "fetch"],
                input=url.encode(),
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=8,
                check=True,
            )
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(len(result.stdout)))
            self.end_headers()
            self.wfile.write(result.stdout)
        except (ValueError, OSError, subprocess.SubprocessError):
            self.send_error(422, "Favicon unavailable")

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    if sys.argv[1:] == ["fetch"]:
        from favicon_fetch import download_favicon

        sys.stdout.buffer.write(download_favicon(sys.stdin.read(2001)))
    else:
        HTTPServer(("0.0.0.0", 8090), Handler).serve_forever()
