"""Test copies must never email a restored real-world recipient."""

from unittest.mock import patch

import pytest
from django.core import mail
from django.core.mail import EmailMultiAlternatives
from django.test import override_settings

from apps.common.mail import transactional
from apps.common.models import EmailSendCounter


@pytest.fixture
def guarded(settings):
    settings.JRC_PROJECT = "jrc-p2b01"
    settings.JRC_TEST_EMAIL_ALLOW = ("approved@example.org",)
    settings.EMAIL_BACKEND = "apps.common.mail.BudgetedEmailBackend"
    settings.EMAIL_INNER_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    settings.EMAIL_SENDING_ENABLED = True


@pytest.mark.django_db
@pytest.mark.parametrize("field", ["to", "cc", "bcc"])
@pytest.mark.parametrize("headers", [None, transactional()])
def test_real_recipient_is_dropped_before_budget_or_smtp(guarded, field, headers, caplog):
    msg = EmailMultiAlternatives(
        subject="Private subject", body="Private body", to=["safe@example.invalid"], headers=headers
    )
    setattr(msg, field, ["private@example.org"])
    with patch("apps.common.mail.BudgetedEmailBackend._inner") as inner:
        assert msg.send() == 0
    inner.assert_not_called()
    assert not EmailSendCounter.objects.exists()
    assert "blocked_recipients=1" in caplog.text
    assert "private@example.org" not in caplog.text
    assert "Private subject" not in caplog.text


@pytest.mark.django_db
@pytest.mark.parametrize(
    "recipient",
    [
        "person@example.invalid",
        "Person <person@sub.EXAMPLE.INVALID>",
        "APPROVED@example.org",
    ],
)
def test_invalid_domains_and_exact_explicit_addresses_are_allowed(guarded, recipient):
    msg = EmailMultiAlternatives(subject="Test", body="body", to=[recipient])
    assert msg.send() == 1
    assert mail.outbox[-1].to == [recipient]


@pytest.mark.django_db
@pytest.mark.parametrize(
    "recipient",
    [
        "person@example.invalid.evil.org",
        "person@notinvalid",
        "person@invalid",
        "safe@example.invalid,private@example.org",
        "safe@example.invalid <private@example.org>",
        "other@example.org",
        "broken",
        "person@example.invalid\nBcc: private@example.org",
    ],
)
def test_suffix_spoofing_malformed_and_unapproved_addresses_fail_closed(guarded, recipient):
    assert EmailMultiAlternatives(subject="s", body="b", to=[recipient]).send() == 0


@pytest.mark.django_db
@pytest.mark.parametrize("project", ["", "jrc-t", "jrc-test-backup"])
def test_unknown_and_other_test_projects_are_guarded(guarded, project):
    with override_settings(JRC_PROJECT=project):
        assert EmailMultiAlternatives(subject="s", body="b", to=["real@example.org"]).send() == 0


@pytest.mark.django_db
def test_main_preserves_existing_mail_behavior(guarded):
    with override_settings(JRC_PROJECT="jrc"):
        assert EmailMultiAlternatives(subject="s", body="b", to=["real@example.org"]).send() == 1
