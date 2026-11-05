"""Route TCP through the local relay while verifying the upstream TLS identity."""

import smtplib
from functools import partial

from django.conf import settings
from django.core.mail.backends.smtp import EmailBackend


class RelayConnection:
    def __init__(self, *args, relay_host, **kwargs):
        self.relay_host = relay_host
        # smtplib keeps the original host for TLS SNI and certificate checks.
        super().__init__(*args, **kwargs)

    def connect(self, host="localhost", port=0, source_address=None):
        return super().connect(self.relay_host or host, port, source_address)


class RelaySMTP(RelayConnection, smtplib.SMTP):
    pass


class RelaySMTPSSL(RelayConnection, smtplib.SMTP_SSL):
    pass


class RelayEmailBackend(EmailBackend):
    @property
    def connection_class(self):
        connection = RelaySMTPSSL if self.use_ssl else RelaySMTP
        return partial(connection, relay_host=getattr(settings, "JRC_SMTP_RELAY_HOST", ""))
