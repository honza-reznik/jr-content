"""Fixed-destination TCP forwarder for the optional Brightbean SMTP route."""

import os
import socket
import socketserver
import threading

TARGET = (os.environ["SMTP_TARGET_HOST"], int(os.environ["SMTP_TARGET_PORT"]))
LISTEN_PORT = int(os.environ["SMTP_LISTEN_PORT"])


def copy_until_closed(source: socket.socket, destination: socket.socket) -> None:
    try:
        while data := source.recv(65536):
            destination.sendall(data)
    except OSError:
        pass
    finally:
        try:
            destination.shutdown(socket.SHUT_WR)
        except OSError:
            pass


class RelayHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        try:
            with socket.create_connection(TARGET, timeout=15) as upstream:
                left = threading.Thread(target=copy_until_closed, args=(self.request, upstream), daemon=True)
                right = threading.Thread(target=copy_until_closed, args=(upstream, self.request), daemon=True)
                left.start()
                right.start()
                left.join()
                right.join()
        except OSError:
            # Do not log SMTP bytes, response text, or connection credentials.
            return


class RelayServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


if __name__ == "__main__":
    with RelayServer(("0.0.0.0", LISTEN_PORT), RelayHandler) as server:
        server.serve_forever(poll_interval=0.5)
