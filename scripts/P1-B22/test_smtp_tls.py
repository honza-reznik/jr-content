"""Local TLS/AUTH integration through the production TCP relay (no internet)."""

import base64
import importlib.util
import json
import os
import socketserver
import ssl
import subprocess
import sys
import threading
from contextlib import contextmanager
from email import policy
from email.parser import BytesParser
from pathlib import Path

import pytest
from django.core.mail import EmailMessage
from django.test import override_settings

pytestmark = pytest.mark.django_db
HOST = "smtp.test.invalid"
PASSWORD = "local-secret-never-log"


@pytest.fixture(scope="module")
def certificate(tmp_path_factory):
    folder = tmp_path_factory.mktemp("smtp-tls")
    cert, key = folder / "cert.pem", folder / "key.pem"
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-days",
            "1",
            "-keyout",
            str(key),
            "-out",
            str(cert),
            "-subj",
            f"/CN={HOST}",
            "-addext",
            f"subjectAltName=DNS:{HOST}",
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=20,
    )
    return cert, key


class SMTPHandler(socketserver.StreamRequestHandler):
    timeout = 5

    def reply(self, data):
        self.wfile.write(data + b"\r\n")
        self.wfile.flush()

    def handle(self):
        self.secure = self.server.implicit
        self.authenticated = False
        self.reply(b"220 local SMTP")
        while line := self.rfile.readline(65536):
            verb = line.split(b" ", 1)[0].strip().upper()
            self.server.commands.append(verb)
            if verb == b"EHLO":
                if self.secure:
                    self.reply(b"250-local\r\n250 AUTH PLAIN")
                else:
                    self.reply(b"250-local\r\n250 STARTTLS")
            elif verb == b"STARTTLS" and not self.secure:
                self.reply(b"220 TLS ready")
                self.connection = self.server.context.wrap_socket(self.connection, server_side=True)
                self.rfile = self.connection.makefile("rb")
                self.wfile = self.connection.makefile("wb")
                self.secure = True
            elif verb == b"AUTH":
                credentials = base64.b64decode(line.split()[2])
                if self.secure and credentials == b"\0local-user\0" + PASSWORD.encode():
                    self.authenticated = True
                    self.reply(b"235 authenticated")
                else:
                    self.reply(b"535 rejected " + PASSWORD.encode())
            elif verb == b"QUIT":
                self.reply(b"221 bye")
                return
            elif not self.authenticated:
                self.reply(b"530 authenticate first")
            elif verb == self.server.reject:
                self.reply(b"550 rejected " + PASSWORD.encode())
            elif verb in {b"MAIL", b"RCPT"}:
                self.server.envelope.append(line.strip())
                self.reply(b"250 accepted")
            elif verb == b"DATA":
                self.reply(b"354 message")
                chunks = []
                while data := self.rfile.readline(65536):
                    if data == b".\r\n":
                        break
                    chunks.append(data[1:] if data.startswith(b"..") else data)
                else:
                    return
                self.server.messages.append(b"".join(chunks))
                self.reply(b"250 queued")
            elif verb == b"RSET":
                self.reply(b"250 reset")
            else:
                self.reply(b"500 unknown command")


class SMTPServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def get_request(self):
        conn, address = super().get_request()
        conn.settimeout(5)
        if self.implicit:
            try:
                conn = self.context.wrap_socket(conn, server_side=True)
            except ssl.SSLError:
                conn.close()
                raise
        return conn, address

    def handle_error(self, request, client_address):
        # Negative tests intentionally abort TLS; never print protocol contents.
        pass


@contextmanager
def local_smtp(certificate, mode, reject=b""):
    cert, key = certificate
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)
    server = SMTPServer(("127.0.0.1", 0), SMTPHandler)
    server.context, server.implicit, server.reject = context, mode == "ssl", reject
    server.messages, server.envelope, server.commands = [], [], []
    # Load the real relay, including its bidirectional byte forwarding.
    spec = importlib.util.spec_from_file_location("jrc_test_relay", "/jrc-relay/relay.py")
    relay_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(relay_module)
    relay_module.TARGET = server.server_address
    relay = relay_module.RelayServer(("127.0.0.1", 0), relay_module.RelayHandler)
    threads = [threading.Thread(target=s.serve_forever, daemon=True) for s in (server, relay)]
    for thread in threads:
        thread.start()
    try:
        with override_settings(
            EMAIL_BACKEND="apps.common.mail.BudgetedEmailBackend",
            EMAIL_INNER_BACKEND="apps.common.smtp.RelayEmailBackend",
            EMAIL_SENDING_ENABLED=True,
            EMAIL_DAILY_SEND_LIMIT=-1,
            EMAIL_RECIPIENT_HOURLY_LIMIT=-1,
            EMAIL_RECIPIENT_DAILY_LIMIT=-1,
            EMAIL_HOST=HOST,
            EMAIL_PORT=relay.server_address[1],
            JRC_SMTP_RELAY_HOST="127.0.0.1",
            EMAIL_HOST_USER="local-user",
            EMAIL_HOST_PASSWORD=PASSWORD,
            EMAIL_USE_SSL=mode == "ssl",
            EMAIL_USE_TLS=mode == "starttls",
            EMAIL_TIMEOUT=5,
            DEFAULT_FROM_EMAIL="sender@example.invalid",
        ):
            yield server
    finally:
        for service in (relay, server):
            service.shutdown()
            service.server_close()
        for thread in threads:
            thread.join(timeout=3)


@pytest.fixture(autouse=True)
def trust_local_certificate(certificate, monkeypatch):
    monkeypatch.setenv("SSL_CERT_FILE", str(certificate[0]))
    monkeypatch.setenv("SMTP_TARGET_HOST", "unused.invalid")
    monkeypatch.setenv("SMTP_TARGET_PORT", "1")
    monkeypatch.setenv("SMTP_LISTEN_PORT", "1")


def message():
    return EmailMessage(
        "Pozvánka – příliš žluťoučký kůň",
        "Řádek česky\n.tečka\nKonec",
        to=["recipient@example.invalid"],
    )


@pytest.mark.parametrize("port,ssl_enabled", [(465, True), (587, False)])
def test_runtime_settings_select_upstream_identity_and_relay(port, ssl_enabled):
    env = {
        **os.environ,
        "DJANGO_SETTINGS_MODULE": "config.settings.jrc",
        "JRC_SMTP_HOST": HOST,
        "JRC_SMTP_RELAY_HOST": "jrc-smtp-relay",
        "JRC_SMTP_PORT": str(port),
        "JRC_SMTP_USER": "local-user",
        "JRC_SMTP_PASSWORD": PASSWORD,
        "JRC_SMTP_FROM": "sender@example.invalid",
    }
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import json; from django.conf import settings as s; "
            "print(json.dumps([s.EMAIL_HOST, s.JRC_SMTP_RELAY_HOST, s.EMAIL_PORT, "
            "s.EMAIL_USE_SSL, s.EMAIL_USE_TLS, s.EMAIL_INNER_BACKEND, s.EMAIL_TIMEOUT]))",
        ],
        env=env,
        check=True,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert json.loads(result.stdout) == [
        HOST,
        "jrc-smtp-relay",
        port,
        ssl_enabled,
        not ssl_enabled,
        "apps.common.smtp.RelayEmailBackend",
        20,
    ]


@pytest.mark.parametrize("mode", ["ssl", "starttls"])
def test_tls_auth_envelope_and_unicode_through_real_relay(certificate, mode, caplog):
    with local_smtp(certificate, mode) as server:
        assert message().send() == 1
    assert len(server.messages) == 1
    mail = BytesParser(policy=policy.default).parsebytes(server.messages[0])
    assert str(mail["Subject"]) == message().subject
    assert mail["From"] == "sender@example.invalid"
    assert mail["To"] == "recipient@example.invalid"
    assert mail.get_content().replace("\r\n", "\n").strip() == message().body
    assert server.envelope == [
        b"mail FROM:<sender@example.invalid>",
        b"rcpt TO:<recipient@example.invalid>",
    ]
    assert b"AUTH" in server.commands and b"DATA" in server.commands
    assert (b"STARTTLS" in server.commands) == (mode == "starttls")
    assert PASSWORD not in caplog.text


@pytest.mark.parametrize("mode", ["ssl", "starttls"])
@pytest.mark.parametrize("failure", ["hostname", "untrusted", "auth", "MAIL", "RCPT", "DATA"])
def test_failures_are_safe_and_certificate_is_still_verified(certificate, mode, failure, caplog, monkeypatch):
    reject = failure.encode() if failure in {"MAIL", "RCPT", "DATA"} else b""
    with local_smtp(certificate, mode, reject) as server:
        overrides = {}
        if failure == "hostname":
            overrides["EMAIL_HOST"] = "jrc-smtp-relay"
        elif failure == "untrusted":
            monkeypatch.setenv("SSL_CERT_FILE", str(Path(ssl.get_default_verify_paths().openssl_cafile)))
        elif failure == "auth":
            overrides["EMAIL_HOST_PASSWORD"] = "wrong-password"
        with override_settings(**overrides), pytest.raises(RuntimeError) as error:
            message().send()
    expected = {
        "hostname": "TLS certificate verification",
        "untrusted": "TLS certificate verification",
        "auth": "SMTP authentication; code=535",
        "MAIL": "SMTP sender refused; code=550",
        "RCPT": "SMTP recipients refused; codes=550",
        "DATA": "SMTP DATA; code=550",
    }[failure]
    assert expected in str(error.value)
    assert expected in caplog.text
    assert PASSWORD not in str(error.value) + caplog.text
    assert "wrong-password" not in str(error.value) + caplog.text
    assert not server.messages
    if failure in {"hostname", "untrusted"}:
        assert b"AUTH" not in server.commands
