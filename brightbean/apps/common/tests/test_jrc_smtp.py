from unittest.mock import patch

import pytest
from django.core.mail import EmailMessage
from django.test import override_settings

pytestmark = pytest.mark.django_db


class FakeSMTP:
    instances = []

    def __init__(self, host, port, **kwargs):
        self.host = host
        self.port = port
        self.kwargs = kwargs
        self.messages = []
        self.login_credentials = None
        type(self).instances.append(self)

    def ehlo(self):
        return 250, b"ok"

    def starttls(self, **kwargs):
        self.tls = True
        return 220, b"ready"

    def login(self, username, password):
        self.login_credentials = (username, password)
        return 235, b"authenticated"

    def sendmail(self, sender, recipients, message):
        self.messages.append((sender, recipients, message))
        return {}

    def quit(self):
        return 221, b"bye"


def test_jrc_smtp_sends_through_local_fake_without_logging_password(caplog):
    FakeSMTP.instances.clear()
    password = "only-for-fake-smtp"
    settings = {
        "EMAIL_BACKEND": "apps.common.mail.BudgetedEmailBackend",
        "EMAIL_INNER_BACKEND": "django.core.mail.backends.smtp.EmailBackend",
        "EMAIL_SENDING_ENABLED": True,
        "EMAIL_DAILY_SEND_LIMIT": -1,
        "EMAIL_RECIPIENT_HOURLY_LIMIT": -1,
        "EMAIL_RECIPIENT_DAILY_LIMIT": -1,
        "EMAIL_HOST": "smtp.invalid",
        "EMAIL_PORT": 587,
        "EMAIL_HOST_USER": "local-user",
        "EMAIL_HOST_PASSWORD": password,
        "EMAIL_USE_TLS": True,
        "EMAIL_USE_SSL": False,
        "DEFAULT_FROM_EMAIL": "noreply@example.invalid",
    }
    with override_settings(**settings), patch("smtplib.SMTP", FakeSMTP):
        sent = EmailMessage("Test", "Message body", to=["recipient@example.invalid"]).send()

    assert sent == 1
    smtp = FakeSMTP.instances[0]
    assert (smtp.host, smtp.port) == ("smtp.invalid", 587)
    assert smtp.tls is True
    assert smtp.login_credentials == ("local-user", password)
    assert smtp.messages[0][0] == "noreply@example.invalid"
    assert smtp.messages[0][1] == ["recipient@example.invalid"]
    assert password not in caplog.text
