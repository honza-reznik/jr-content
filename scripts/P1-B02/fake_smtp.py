"""Small plaintext SMTP sink for the local-only relay regression test."""

import socketserver

COUNT_FILE = "/tmp/inbox-count"


class SMTPHandler(socketserver.StreamRequestHandler):
    def handle(self):
        self.wfile.write(b"220 local test SMTP ready\r\n")
        self.wfile.flush()
        while line := self.rfile.readline():
            command = line.decode("ascii", "replace").strip()
            verb = command.split(" ", 1)[0].upper()
            if verb in {"EHLO", "HELO"}:
                self.wfile.write(b"250-local test SMTP\r\n250 AUTH PLAIN LOGIN\r\n")
            elif verb == "AUTH":
                self.wfile.write(b"235 authenticated\r\n")
            elif verb in {"MAIL", "RCPT"}:
                self.wfile.write(b"250 accepted\r\n")
            elif verb == "DATA":
                self.wfile.write(b"354 send data\r\n")
                self.wfile.flush()
                while self.rfile.readline().strip() != b".":
                    pass
                try:
                    with open(COUNT_FILE, "r+") as count_file:
                        count = int(count_file.read() or "0") + 1
                        count_file.seek(0)
                        count_file.write(str(count))
                        count_file.truncate()
                except FileNotFoundError:
                    with open(COUNT_FILE, "w") as count_file:
                        count_file.write("1")
                self.wfile.write(b"250 queued\r\n")
            elif verb == "QUIT":
                self.wfile.write(b"221 bye\r\n")
                self.wfile.flush()
                return
            else:
                self.wfile.write(b"250 ok\r\n")
            self.wfile.flush()


class SMTPServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


if __name__ == "__main__":
    with SMTPServer(("0.0.0.0", 2525), SMTPHandler) as server:
        server.serve_forever()
