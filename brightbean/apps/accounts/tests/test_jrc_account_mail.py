"""JR Content account mail and the complete invite/signup round trip."""

import re
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

import allauth
import pytest
from allauth.account.adapter import get_adapter
from django.contrib.sites.models import Site
from django.core import mail
from django.test import RequestFactory
from django.urls import reverse, set_script_prefix
from django.utils import timezone

from apps.accounts.models import User
from apps.common.testing import run_email_queue
from apps.members.models import Invitation, OrgMembership
from apps.members.services import _send_invite_email
from apps.organizations.models import Organization


def _owner_with_org(email="owner@example.invalid", **extra):
    """Vlastník s výslovně založenou organizací (registrace organizaci nezakládá – P1-B23)."""
    owner = User.objects.create_user(email=email, **extra)
    org = Organization.objects.create(name="Test Organization")
    OrgMembership.objects.create(user=owner, organization=org, org_role=OrgMembership.OrgRole.OWNER)
    return owner, org


@pytest.fixture
def public_mail(settings):
    settings.JRC_PUBLIC_ORIGIN = "https://content.example.invalid"
    settings.JRC_BASE_PATH = "/jrc"
    settings.FORCE_SCRIPT_NAME = "/jrc"
    settings.APP_URL = settings.JRC_PUBLIC_ORIGIN
    settings.DEFAULT_FROM_EMAIL = "sender@example.invalid"
    settings.SIGNUP_MODE = "invite"
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    Site.objects.update_or_create(pk=settings.SITE_ID, defaults={"name": "Brightbean", "domain": "old.invalid"})
    set_script_prefix("/jrc/")
    yield settings
    set_script_prefix("/")


def assert_mail(message):
    assert "JR Content" in message.subject
    assert message.from_email == "sender@example.invalid"
    content = "\n".join([message.subject, message.body, *(a[0] for a in message.alternatives)])
    assert "brightbean" not in content.lower()
    assert "old.invalid" not in content
    urls = re.findall(r'https?://[^\s<>"\']+', content)
    assert urls
    for url in urls:
        parsed = urlsplit(url.rstrip("."))
        assert parsed.scheme == "https"
        assert parsed.netloc == "content.example.invalid"
        assert parsed.path.startswith("/jrc/")
        assert "/jrc/jrc/" not in parsed.path
    return content


EMAIL_KINDS = sorted(
    p.name.removesuffix("_subject.txt")
    for p in (Path(allauth.__file__).parent / "templates/account/email").glob("*_subject.txt")
)


@pytest.mark.django_db
@pytest.mark.parametrize("kind", EMAIL_KINDS)
def test_every_allauth_email(public_mail, kind):
    user = User.objects.create_user(email="account@example.invalid")
    request = RequestFactory().get("/", HTTP_HOST="localhost:8000")
    context = {
        "request": request,
        "user": user,
        "email": user.email,
        "current_site": SimpleNamespace(name="Brightbean", domain="old.invalid"),
        "activate_url": "http://localhost:8000/accounts/confirm-email/key/",
        "password_reset_url": "http://localhost:8000/jrc/accounts/password/reset/key/",
        "signup_url": "http://localhost:8000/accounts/signup/",
        "code": "123456",
        "from_email": "previous@example.invalid",
        "to_email": user.email,
        "deleted_email": "previous@example.invalid",
    }
    get_adapter().send_mail("account/email/" + kind, user.email, context)
    run_email_queue()
    content = assert_mail(mail.outbox[-1])
    if kind == "unknown_account":
        assert "/accounts/signup/" not in content
        assert "invitation" in content.lower()


@pytest.mark.django_db
def test_invitation_mail(public_mail):
    owner, org = _owner_with_org()
    invite = Invitation.objects.create(
        organization=org,
        invited_by=owner,
        email="invite@example.invalid",
        expires_at=timezone.now() + timedelta(days=7),
    )
    assert _send_invite_email(invite)
    run_email_queue()
    assert_mail(mail.outbox[-1])


@pytest.mark.django_db
def test_signup_returns_to_accepted_invite(settings, client):
    settings.SIGNUP_MODE = "invite"
    settings.ACCOUNT_EMAIL_VERIFICATION = "none"
    settings.JRC_FEATURES = {"terms": False}
    owner, org = _owner_with_org()
    invite = Invitation.objects.create(
        organization=org, invited_by=owner, email="new@example.invalid", expires_at=timezone.now() + timedelta(days=7)
    )
    url = reverse("members:accept_invite", args=[invite.token])
    client.get(url)
    response = client.post(
        reverse("account_signup") + "?next=" + url,
        {"email": invite.email, "password1": "Only-local-P1b24-password!"},
        follow=True,
    )
    assert response.status_code == 200
    assert b"Invitation Expired" not in response.content
    invite.refresh_from_db()
    user = User.objects.get(email=invite.email)
    assert invite.accepted_by_id == user.pk
    assert OrgMembership.objects.filter(user=user, organization=org).exists()
    assert client.get(url).status_code == 302
    client.logout()
    assert b"Invitation Expired" in client.get(url).content
    client.force_login(owner)
    assert b"Invitation Expired" in client.get(url).content
    client.force_login(user)
    OrgMembership.objects.filter(user=user, organization=org).delete()
    assert b"Invitation Expired" in client.get(url).content


@pytest.mark.django_db
def test_historical_accepted_invite_stays_closed(client):
    owner, org = _owner_with_org(tos_accepted_at=timezone.now())
    invite = Invitation.objects.create(
        organization=org,
        email=owner.email,
        expires_at=timezone.now() + timedelta(days=7),
        accepted_at=timezone.now(),
    )
    client.force_login(owner)
    assert b"Invitation Expired" in client.get(reverse("members:accept_invite", args=[invite.token])).content


@pytest.mark.django_db
@pytest.mark.parametrize("prefix", ["", "/jrc"])
@pytest.mark.parametrize("mode", ["open", "invite", "closed"])
def test_unknown_account_without_request(settings, prefix, mode):
    settings.JRC_PUBLIC_ORIGIN = "https://content.example.invalid"
    settings.JRC_BASE_PATH = prefix
    settings.SIGNUP_MODE = mode
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    get_adapter().send_mail(
        "account/email/unknown_account",
        "nobody@example.invalid",
        {"email": "nobody@example.invalid", "signup_url": "http://old.invalid/accounts/signup/"},
    )
    run_email_queue()
    body = mail.outbox[-1].body
    assert ("/accounts/signup/" in body) == (mode == "open")
    assert "https://content.example.invalid" + prefix + "/" in body
    assert "old.invalid" not in body


@pytest.mark.django_db
def test_notification_and_digest_brand(public_mail):
    from apps.notifications.engine import _dispatch_email, _send_digest_email
    from apps.notifications.models import EventType, Notification, NotificationDelivery

    user = User.objects.create_user(email="notice@example.invalid")
    notification = Notification.objects.create(
        user=user,
        event_type=EventType.POST_FAILED,
        title="Please check this post",
        data={"action_url": "/notifications/"},
    )
    _dispatch_email(NotificationDelivery(notification=notification, channel="email"))
    _send_digest_email(user, EventType.POST_FAILED, [notification])
    for message in mail.outbox:
        assert message.from_email == public_mail.DEFAULT_FROM_EMAIL
        assert "brightbean" not in message.message().as_string().lower()
        for content in [message.body, message.alternatives[0][0]]:
            assert "JR Content" in content
            pattern = r'href=["\'](https?://[^"\']+)' if "<html" in content else r'https?://[^\s<>"\']+'
            for url in re.findall(pattern, content):
                assert url.startswith("https://content.example.invalid/jrc/")
