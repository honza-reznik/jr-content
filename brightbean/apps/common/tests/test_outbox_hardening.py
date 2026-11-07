"""P1-B35: the outbox after review: bodies age out, the callback is part of closing the row,
a resend retires the older invitation email, and nothing sends mail from a request."""

import pathlib
import re
import statistics
import time
from datetime import timedelta

import pytest
from background_task.models import Task
from django.contrib.auth import get_user_model
from django.core import mail
from django.core.mail.backends import locmem
from django.urls import reverse
from django.utils import timezone

from apps.common import outbox
from apps.common.models import OutgoingEmail
from apps.common.testing import run_email_queue
from apps.common.tests.test_outbox import SLOW, SLOW_SECONDS, budgeted, make_due, message
from apps.members.models import Invitation, OrgMembership
from apps.organizations.models import Organization

CALLS = []
STATE = {"fail": True}


def callback_that_writes_then_fails(org_id):
    """Writes first, so the test can see the write being undone with the row."""
    Organization.objects.filter(pk=org_id).update(name="touched by callback")
    CALLS.append(org_id)
    if STATE["fail"]:
        raise RuntimeError("callback broke")


@pytest.fixture(autouse=True)
def _clean(request):
    CALLS.clear()
    STATE["fail"] = True
    if request.node.get_closest_marker("django_db"):
        Task.objects.all().delete()


@pytest.fixture
def org_and_owner(db):
    org = Organization.objects.create(name="Org")
    owner = get_user_model().objects.create_user(
        email="owner@example.com", password="pw-that-is-long", name="Owner", tos_accepted_at=timezone.now()
    )
    OrgMembership.objects.create(user=owner, organization=org, org_role=OrgMembership.OrgRole.OWNER)
    return org, owner


# ---- AK1: a pending row does not keep its one-time link for ever ----------------------------------


@pytest.mark.django_db(transaction=True)
@budgeted()
def test_a_pending_email_older_than_the_cap_is_failed_unsent_and_its_body_erased():
    row = outbox.queue_email(message(subject="Reset your password"))
    OutgoingEmail.objects.filter(pk=row.pk).update(created_at=timezone.now() - outbox.MAX_PENDING_AGE - timedelta(1))
    Task.objects.all().delete()
    make_due(row)

    assert outbox.sweep() == 1
    run_email_queue()

    row.refresh_from_db()
    assert mail.outbox == []
    assert row.status == "failed" and "waited too long" in row.last_error
    assert row.body_text == "" and row.alternatives == [] and row.headers == {}


@pytest.mark.django_db(transaction=True)
@budgeted()
def test_the_age_cap_reports_the_failure_to_the_caller_once():
    org = Organization.objects.create(name="Org")
    row = outbox.queue_email(
        message(),
        on_failure="apps.common.tests.test_outbox_hardening:callback_that_writes_then_fails",
        callback_args={"org_id": str(org.pk)},
    )
    STATE["fail"] = False
    OutgoingEmail.objects.filter(pk=row.pk).update(created_at=timezone.now() - outbox.MAX_PENDING_AGE - timedelta(1))
    run_email_queue()
    run_email_queue()
    assert [str(org.pk)] == CALLS and mail.outbox == []


@pytest.mark.django_db(transaction=True)
@budgeted()
def test_a_young_pending_email_is_still_sent():
    outbox.queue_email(message())
    run_email_queue()
    assert len(mail.outbox) == 1


# ---- AK2: closing the row and the callback are one transaction ------------------------------------


@pytest.mark.django_db(transaction=True)
@budgeted()
def test_a_failing_callback_undoes_the_close_and_the_retry_only_repeats_the_callback():
    org = Organization.objects.create(name="Org")
    row = outbox.queue_email(
        message(),
        on_success="apps.common.tests.test_outbox_hardening:callback_that_writes_then_fails",
        callback_args={"org_id": str(org.pk)},
    )

    assert outbox.deliver(str(row.pk)) == "sent"
    row.refresh_from_db()
    # The callback's own write went away with the failed transaction; the row is not closed.
    assert Organization.objects.get(pk=org.pk).name == "Org"
    assert row.status == "pending" and row.finishing == "sent"
    assert len(mail.outbox) == 1

    STATE["fail"] = False
    make_due(row)
    assert outbox.deliver(str(row.pk)) == "sent"
    row.refresh_from_db()
    assert row.status == "sent" and row.finishing == ""
    assert Organization.objects.get(pk=org.pk).name == "touched by callback"
    assert len(mail.outbox) == 1, "the message was not sent again"
    assert row.body_text == ""

    make_due(row)
    assert outbox.deliver(str(row.pk)) == "skipped"
    assert len(CALLS) == 2  # failed once, took effect once


@pytest.mark.django_db(transaction=True)
@budgeted()
def test_a_failure_callback_that_raises_is_repeated_without_sending_the_message():
    org = Organization.objects.create(name="Org")
    row = outbox.queue_email(
        message(),
        on_failure="apps.common.tests.test_outbox_hardening:callback_that_writes_then_fails",
        callback_args={"org_id": str(org.pk)},
    )
    OutgoingEmail.objects.filter(pk=row.pk).update(attempts=outbox.MAX_ATTEMPTS - 1)
    with budgeted(EMAIL_SENDING_ENABLED=False):
        assert outbox.deliver(str(row.pk)) == "failed"
    row.refresh_from_db()
    assert row.status == "pending" and row.finishing == "failed"

    STATE["fail"] = False
    make_due(row)
    # Even with sending switched on again, only the bookkeeping is repeated.
    assert outbox.deliver(str(row.pk)) == "failed"
    row.refresh_from_db()
    assert row.status == "failed" and mail.outbox == []


@pytest.mark.django_db(transaction=True)
@budgeted()
def test_a_callback_that_never_works_is_given_up_and_the_body_is_still_erased():
    org = Organization.objects.create(name="Org")
    row = outbox.queue_email(
        message(),
        on_success="apps.common.tests.test_outbox_hardening:callback_that_writes_then_fails",
        callback_args={"org_id": str(org.pk)},
    )
    for _ in range(outbox.MAX_FINISH_ATTEMPTS + 2):
        make_due(row)
        outbox.deliver(str(row.pk))
    row.refresh_from_db()
    assert row.status == "sent" and row.body_text == ""
    assert len(mail.outbox) == 1
    assert len(CALLS) == outbox.MAX_FINISH_ATTEMPTS - 1


# ---- AK3: invitation bookkeeping ------------------------------------------------------------------


def make_invitation(org, owner, email="invitee@example.com"):
    from apps.members.services import create_invitation

    return create_invitation(
        org=org,
        email=email,
        org_role=OrgMembership.OrgRole.MEMBER,
        workspace_assignments=[],
        invited_by=owner,
        inviter=owner,
    )


@pytest.mark.django_db(transaction=True)
@budgeted()
def test_releasing_a_failed_slot_does_not_overwrite_a_newer_send_time(org_and_owner):
    from apps.members.services import release_invite_send_after_failure

    org, owner = org_and_owner
    invitation = make_invitation(org, owner)
    reserved = invitation.last_sent_at
    # A later send took the slot after the first email had been queued.
    newer = reserved + timedelta(minutes=10)
    Invitation.objects.filter(pk=invitation.pk).update(last_sent_at=newer, send_count=2)

    release_invite_send_after_failure(str(invitation.pk), None, reserved.isoformat())

    fresh = Invitation.objects.get(pk=invitation.pk)
    assert fresh.send_count == 1, "the failed email gave its own slot back"
    assert fresh.last_sent_at == newer, "the newer send keeps its cooldown"


@pytest.mark.django_db(transaction=True)
@budgeted()
def test_releasing_the_latest_slot_still_restores_the_previous_time(org_and_owner):
    from apps.members.services import release_invite_send_after_failure

    org, owner = org_and_owner
    invitation = make_invitation(org, owner)
    reserved = invitation.last_sent_at
    release_invite_send_after_failure(str(invitation.pk), None, reserved.isoformat())
    fresh = Invitation.objects.get(pk=invitation.pk)
    assert fresh.send_count == 0 and fresh.last_sent_at is None


@pytest.mark.django_db(transaction=True)
@budgeted()
def test_a_resend_cancels_the_older_waiting_email_whose_token_no_longer_works(org_and_owner):
    from apps.members.services import resend_invitation

    org, owner = org_and_owner
    invitation = make_invitation(org, owner)
    old_token = invitation.token
    old_row = OutgoingEmail.objects.get()
    assert old_token in old_row.body_text

    # The first email is still waiting (worker slow); the cooldown has passed.
    Invitation.objects.filter(pk=invitation.pk).update(last_sent_at=timezone.now() - timedelta(minutes=10))
    invitation.refresh_from_db()
    resend_invitation(invitation)

    old_row.refresh_from_db()
    assert old_row.status == "failed" and old_row.last_error.startswith("cancelled")
    assert old_row.body_text == "" and old_row.alternatives == []

    run_email_queue()
    assert len(mail.outbox) == 1
    invitation.refresh_from_db()
    assert invitation.token != old_token and invitation.token in mail.outbox[0].body
    assert old_token not in mail.outbox[0].body


@pytest.mark.django_db(transaction=True)
@budgeted()
def test_cancel_pending_touches_only_matching_waiting_rows():
    a = outbox.queue_email(message(), callback_args={"invitation_id": "a"})
    b = outbox.queue_email(message(), callback_args={"invitation_id": "b"})
    assert outbox.cancel_pending(invitation_id="a") == 1
    a.refresh_from_db()
    b.refresh_from_db()
    assert a.status == "failed" and b.status == "pending"
    assert outbox.cancel_pending(invitation_id="a") == 0


# ---- AK5: nothing in a request reaches the mail server --------------------------------------------

TRAP_CALLS = []


class TrapBackend(locmem.EmailBackend):
    """Counts every attempt to talk to a mail server, then delivers into the local outbox."""

    def send_messages(self, messages):
        TRAP_CALLS.append([m.subject for m in messages])
        return super().send_messages(messages)


TRAP = "apps.common.tests.test_outbox_hardening.TrapBackend"

# Every way Django offers to send: a message's own ``send``, the shortcut functions, a connection,
# and the outbox's own helper. Only the worker side may contain them.
SEND_PATTERN = re.compile(
    r"\.send\(|\bsend_mail\(|\bsend_mass_mail\(|\bmail_admins\(|\bmail_managers\(|\.send_messages\(|"
    r"\bsend_or_raise\(|\bget_connection\(|\bopen_connection\(|\.open\(\)\s*#\s*smtp"
)
WORKER_SIDE_FILES = {
    "common/outbox.py",
    "common/mail.py",
    "notifications/engine.py",
}


def test_only_the_worker_side_code_sends_mail_by_any_route():
    import apps

    root = pathlib.Path(apps.__file__).parent
    offenders = []
    for path in root.rglob("*.py"):
        relative = path.relative_to(root).as_posix()
        if relative in WORKER_SIDE_FILES or "tests" in path.parts or path.name.startswith("test_"):
            continue
        if path.name in ("testing.py", "conftest.py") or "migrations" in path.parts:
            continue
        for number, line in enumerate(path.read_text().splitlines(), 1):
            if line.lstrip().startswith(("#", "def ")):
                continue
            if SEND_PATTERN.search(line):
                offenders.append(f"{relative}:{number}: {line.strip()}")
    assert offenders == []


@pytest.mark.django_db(transaction=True)
@budgeted(inner=TRAP)
def test_no_request_reaches_the_mail_backend(client, org_and_owner):
    """Behavioural twin of the scan: password reset, invitation, magic link and a notification in a
    request leave the backend untouched until the worker runs."""
    from apps.client_portal.services import generate_magic_link
    from apps.members.models import WorkspaceMembership
    from apps.notifications.engine import notify
    from apps.notifications.models import EventType
    from apps.workspaces.models import Workspace

    TRAP_CALLS.clear()
    org, owner = org_and_owner
    client.post(reverse("account_reset_password"), {"email": owner.email})
    client.post(reverse("account_reset_password"), {"email": "nobody@example.com"})
    make_invitation(org, owner)
    workspace = Workspace.objects.create(organization=org, name="Client")
    client_user = get_user_model().objects.create_user(
        email="client@example.com", password="pw-that-is-long", name="C", tos_accepted_at=timezone.now()
    )
    WorkspaceMembership.objects.create(workspace=workspace, user=client_user, workspace_role="client")
    generate_magic_link(workspace, client_user, owner)
    notify(owner, EventType.POST_REJECTED, "Post rejected", "Because.")

    assert TRAP_CALLS == [], "a request talked to the mail backend"
    run_email_queue()
    assert len(TRAP_CALLS) >= 5


# ---- AK5: the reset time does not betray whether an address has an account ------------------------


@pytest.mark.django_db(transaction=True)
@budgeted(inner=SLOW)
def test_reset_time_median_of_20_is_the_same_for_known_and_unknown_in_both_orders(client, settings, monkeypatch):
    get_user_model().objects.create_user(
        email="known@example.com", password="pw-that-is-long", name="K", tos_accepted_at=timezone.now()
    )
    # The form's own rate limit (per address and per client) would answer 429 long before 40
    # requests; it is not what is measured here.
    settings.ACCOUNT_RATE_LIMITS = False
    monkeypatch.setattr("apps.accounts.middleware.AUTH_RATE_LIMIT", 10_000)

    def measure(address):
        started = time.monotonic()
        response = client.post(reverse("account_reset_password"), {"email": address})
        elapsed = time.monotonic() - started
        assert response.status_code in (200, 302), response.status_code
        return elapsed

    results = {}
    for order in (("known@example.com", "nobody@example.com"), ("nobody@example.com", "known@example.com")):
        samples = {address: [] for address in order}
        for _ in range(20):
            for address in order:
                samples[address].append(measure(address))
        results[order] = {address: statistics.median(values) for address, values in samples.items()}

    assert mail.outbox == []
    for order, medians in results.items():
        assert max(medians.values()) < SLOW_SECONDS / 4, (order, medians)
        known = medians["known@example.com"]
        unknown = medians["nobody@example.com"]
        assert abs(known - unknown) < 0.1, (order, medians)
