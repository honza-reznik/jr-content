"""P1-B33: no request sends mail; the worker does, once, with retries, after commit."""

import smtplib
import time
from datetime import timedelta

import pytest
from background_task.models import Task
from django.contrib.auth import get_user_model
from django.core import mail
from django.core.mail import EmailMultiAlternatives
from django.core.mail.backends import locmem
from django.db import transaction
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from apps.common import outbox
from apps.common.models import OutgoingEmail
from apps.common.testing import run_email_queue
from apps.members.models import OrgMembership
from apps.organizations.models import Organization

SLOW_SECONDS = 1.0
CALLS = []


class SlowBackend(locmem.EmailBackend):
    """A mail server that takes a second to answer."""

    def send_messages(self, messages):
        time.sleep(SLOW_SECONDS)
        return super().send_messages(messages)


class FlakyBackend(locmem.EmailBackend):
    """Refuses the first ``failures`` attempts, like a server that is briefly unreachable."""

    failures = 0

    def send_messages(self, messages):
        if FlakyBackend.failures > 0:
            FlakyBackend.failures -= 1
            raise smtplib.SMTPServerDisconnected("connection lost, password=hunter2")
        return super().send_messages(messages)


def budgeted(inner="django.core.mail.backends.locmem.EmailBackend", **extra):
    settings = {
        "EMAIL_BACKEND": "apps.common.mail.BudgetedEmailBackend",
        "EMAIL_INNER_BACKEND": inner,
        "EMAIL_SENDING_ENABLED": True,
        "EMAIL_DAILY_SEND_LIMIT": -1,
        "EMAIL_RECIPIENT_HOURLY_LIMIT": -1,
        "EMAIL_RECIPIENT_DAILY_LIMIT": -1,
    }
    settings.update(extra)
    return override_settings(**settings)


FLAKY = "apps.common.tests.test_outbox.FlakyBackend"
SLOW = "apps.common.tests.test_outbox.SlowBackend"


def message(to="person@example.com", subject="Hello"):
    msg = EmailMultiAlternatives(
        subject=subject, body="plain body", from_email="noreply@example.com", to=[to], headers={"X-Test": "1"}
    )
    msg.attach_alternative("<p>html body</p>", "text/html")
    return msg


def record_success(**kwargs):
    CALLS.append(("ok", kwargs))


def record_failure(**kwargs):
    CALLS.append(("failed", kwargs))


@pytest.fixture(autouse=True)
def _clean():
    CALLS.clear()
    FlakyBackend.failures = 0
    Task.objects.all().delete()
    yield


def make_due(row):
    OutgoingEmail.objects.filter(pk=row.pk).update(next_attempt_at=timezone.now() - timedelta(seconds=1))


@pytest.mark.django_db(transaction=True)
@budgeted()
def test_queueing_sends_nothing_and_the_worker_delivers_the_whole_message():
    row = outbox.queue_email(message())

    assert mail.outbox == []
    assert run_email_queue() == 1
    sent = mail.outbox
    assert len(sent) == 1
    assert sent[0].subject == "Hello" and sent[0].to == ["person@example.com"]
    assert sent[0].body == "plain body" and sent[0].alternatives == [("<p>html body</p>", "text/html")]
    assert sent[0].extra_headers.get("X-Test") == "1"
    row.refresh_from_db()
    assert row.status == "sent" and row.sent_at is not None


@pytest.mark.django_db(transaction=True)
@budgeted()
def test_a_finished_email_keeps_no_body_with_its_one_time_link():
    row = outbox.queue_email(message(subject="Reset"))
    run_email_queue()
    row.refresh_from_db()
    assert row.body_text == "" and row.alternatives == [] and row.headers == {}


@pytest.mark.django_db(transaction=True)
@budgeted()
def test_a_rolled_back_transaction_leaves_no_email_and_no_task():
    with pytest.raises(RuntimeError), transaction.atomic():
        outbox.queue_email(message())
        raise RuntimeError("the request failed after queueing")

    assert OutgoingEmail.objects.count() == 0
    assert run_email_queue() == 0
    assert mail.outbox == []


@pytest.mark.django_db(transaction=True)
@budgeted()
def test_the_task_is_invisible_to_the_worker_until_the_transaction_commits():
    with transaction.atomic():
        outbox.queue_email(message())
        # Another connection would not see it yet; inside it, the row and the task exist together.
        assert OutgoingEmail.objects.count() == 1 and Task.objects.count() == 1
    assert run_email_queue() == 1 and len(mail.outbox) == 1


@pytest.mark.django_db(transaction=True)
@budgeted(inner=FLAKY)
def test_a_transient_failure_is_retried_later_and_delivered_exactly_once():
    FlakyBackend.failures = 2
    row = outbox.queue_email(message())

    run_email_queue()
    row.refresh_from_db()
    assert row.status == "pending" and row.attempts == 1
    assert row.next_attempt_at > timezone.now()
    assert "hunter2" not in row.last_error and "SMTP delivery failed" in row.last_error
    assert Task.objects.filter(task_name="apps.common.tasks.deliver_outgoing_email").exists(), "retry is queued"
    assert mail.outbox == []

    # Not yet due: running the queue does nothing.
    assert run_email_queue() == 0 and mail.outbox == []

    make_due(row)
    assert outbox.deliver(str(row.pk)) == "retry"  # second failure
    make_due(row)
    assert outbox.deliver(str(row.pk)) == "sent"
    assert len(mail.outbox) == 1
    # Late duplicates of the task (the retry task, a sweep) find nothing to do.
    assert outbox.deliver(str(row.pk)) == "skipped"
    run_email_queue()
    assert len(mail.outbox) == 1


@pytest.mark.django_db(transaction=True)
@budgeted(inner=FLAKY)
def test_it_gives_up_after_the_attempt_cap_and_reports_the_failure_once():
    FlakyBackend.failures = 99
    row = outbox.queue_email(
        message(),
        on_success="apps.common.tests.test_outbox:record_success",
        on_failure="apps.common.tests.test_outbox:record_failure",
        callback_args={"key": "v"},
    )
    for _ in range(outbox.MAX_ATTEMPTS + 2):
        make_due(row)
        outbox.deliver(str(row.pk))

    row.refresh_from_db()
    assert row.status == "failed" and row.attempts == outbox.MAX_ATTEMPTS
    assert CALLS == [("failed", {"key": "v"})]
    assert mail.outbox == []


@pytest.mark.django_db(transaction=True)
@budgeted(EMAIL_SENDING_ENABLED=False)
def test_a_message_the_budget_declines_is_final_and_not_retried():
    row = outbox.queue_email(message(), on_failure="apps.common.tests.test_outbox:record_failure")
    run_email_queue()

    row.refresh_from_db()
    assert row.status == "failed" and row.attempts == 1
    assert [c[0] for c in CALLS] == ["failed"]
    assert not Task.objects.filter(task_name="apps.common.tasks.deliver_outgoing_email").exists()


@pytest.mark.django_db(transaction=True)
@budgeted()
def test_the_success_callback_runs_once():
    outbox.queue_email(message(), on_success="apps.common.tests.test_outbox:record_success", callback_args={"n": 1})
    run_email_queue()
    run_email_queue()
    assert CALLS == [("ok", {"n": 1})]


@pytest.mark.django_db(transaction=True)
@budgeted()
def test_callbacks_must_live_in_the_application():
    with pytest.raises(ValueError):
        outbox.queue_email(message(), on_failure="os:system")


@pytest.mark.django_db(transaction=True)
@budgeted()
def test_a_row_being_sent_is_not_sent_again_by_the_sweep_but_is_after_the_lease():
    row = outbox.queue_email(message())
    Task.objects.all().delete()  # the task is lost (restart, deleted row)

    # Claim like a worker that then dies mid-send.
    OutgoingEmail.objects.filter(pk=row.pk).update(attempts=1, next_attempt_at=timezone.now() + outbox.LEASE)
    assert outbox.sweep() == 0
    assert outbox.deliver(str(row.pk)) == "skipped"

    OutgoingEmail.objects.filter(pk=row.pk).update(next_attempt_at=timezone.now() - timedelta(seconds=1))
    assert outbox.sweep() == 1
    run_email_queue()
    assert len(mail.outbox) == 1


@pytest.mark.django_db(transaction=True)
@budgeted()
def test_the_sweep_recovers_an_email_whose_task_was_lost_and_forgets_old_finished_ones():
    lost = outbox.queue_email(message(subject="lost"))
    Task.objects.all().delete()
    old = outbox.queue_email(message(subject="old"))
    run_email_queue()
    OutgoingEmail.objects.filter(pk=old.pk).update(created_at=timezone.now() - outbox.KEEP_FINISHED - timedelta(days=1))
    mail.outbox.clear()

    assert outbox.sweep() == 1
    run_email_queue()
    assert [m.subject for m in mail.outbox] == ["lost"]
    assert not OutgoingEmail.objects.filter(pk=old.pk).exists()
    assert OutgoingEmail.objects.filter(pk=lost.pk, status="sent").exists()


# ---- the places that used to send in the request -------------------------------------------------


@pytest.fixture
def org_and_owner(db):
    org = Organization.objects.create(name="Org")
    owner = get_user_model().objects.create_user(
        email="owner@example.com", password="pw-that-is-long", name="Owner", tos_accepted_at=timezone.now()
    )
    OrgMembership.objects.create(user=owner, organization=org, org_role=OrgMembership.OrgRole.OWNER)
    return org, owner


@pytest.mark.django_db(transaction=True)
@budgeted(inner=SLOW)
def test_an_invitation_returns_at_once_and_the_worker_sends_it(org_and_owner):
    from apps.members.services import create_invitation

    org, owner = org_and_owner
    # The first call compiles templates and imports modules; the rule is about the mail server, not that.
    create_invitation(
        org=org,
        email="warmup@example.com",
        org_role=OrgMembership.OrgRole.MEMBER,
        workspace_assignments=[],
        invited_by=owner,
        inviter=owner,
    )
    started = time.monotonic()
    invitation = create_invitation(
        org=org,
        email="invitee@example.com",
        org_role=OrgMembership.OrgRole.MEMBER,
        workspace_assignments=[],
        invited_by=owner,
        inviter=owner,
    )
    assert time.monotonic() - started < SLOW_SECONDS / 2
    assert invitation.last_sent_at is not None and invitation.send_count == 1
    assert mail.outbox == []

    run_email_queue()
    assert sorted(m.to[0] for m in mail.outbox) == ["invitee@example.com", "warmup@example.com"]


@pytest.mark.django_db(transaction=True)
@budgeted(inner=FLAKY)
def test_an_invitation_that_finally_fails_gives_its_send_slot_back(org_and_owner):
    from apps.members.models import Invitation
    from apps.members.services import create_invitation

    FlakyBackend.failures = 99
    org, owner = org_and_owner
    invitation = create_invitation(
        org=org,
        email="invitee@example.com",
        org_role=OrgMembership.OrgRole.MEMBER,
        workspace_assignments=[],
        invited_by=owner,
        inviter=owner,
    )
    for _ in range(outbox.MAX_ATTEMPTS):
        OutgoingEmail.objects.update(next_attempt_at=timezone.now() - timedelta(seconds=1))
        for row in OutgoingEmail.objects.all():
            outbox.deliver(str(row.pk))

    fresh = Invitation.objects.get(pk=invitation.pk)
    assert fresh.send_count == 0 and fresh.last_sent_at is None
    assert mail.outbox == []


@pytest.mark.django_db(transaction=True)
@budgeted(inner=SLOW)
def test_password_reset_takes_the_same_short_time_for_a_known_and_an_unknown_address(client):
    get_user_model().objects.create_user(
        email="known@example.com", password="pw-that-is-long", name="K", tos_accepted_at=timezone.now()
    )
    timings = {}
    for label, address in (("known", "known@example.com"), ("unknown", "nobody@example.com")):
        started = time.monotonic()
        response = client.post(reverse("account_reset_password"), {"email": address})
        timings[label] = time.monotonic() - started
        assert response.status_code in (200, 302)

    assert mail.outbox == []
    assert max(timings.values()) < SLOW_SECONDS / 2, timings
    assert abs(timings["known"] - timings["unknown"]) < SLOW_SECONDS / 2, timings

    run_email_queue()
    assert {tuple(m.to) for m in mail.outbox} == {("known@example.com",), ("nobody@example.com",)}


@pytest.mark.django_db(transaction=True)
@budgeted(inner=SLOW)
def test_a_magic_link_keeps_the_old_link_until_the_new_one_is_sent(org_and_owner):
    from apps.client_portal.models import MagicLinkToken
    from apps.client_portal.services import generate_magic_link
    from apps.members.models import WorkspaceMembership
    from apps.workspaces.models import Workspace

    org, owner = org_and_owner
    workspace = Workspace.objects.create(organization=org, name="Client")
    client_user = get_user_model().objects.create_user(
        email="client@example.com", password="pw-that-is-long", name="C", tos_accepted_at=timezone.now()
    )
    WorkspaceMembership.objects.create(workspace=workspace, user=client_user, workspace_role="client")

    first = generate_magic_link(workspace, client_user, owner)
    run_email_queue()
    mail.outbox.clear()

    started = time.monotonic()
    second = generate_magic_link(workspace, client_user, owner)
    assert time.monotonic() - started < SLOW_SECONDS / 2
    assert MagicLinkToken.objects.get(pk=first.pk).expires_at > timezone.now(), "old link still works"

    run_email_queue()
    assert len(mail.outbox) == 1
    assert MagicLinkToken.objects.get(pk=first.pk).expires_at <= timezone.now()
    assert MagicLinkToken.objects.filter(pk=second.pk).exists()


@pytest.mark.django_db(transaction=True)
@budgeted(EMAIL_SENDING_ENABLED=False)
def test_a_magic_link_that_is_not_sent_is_discarded_and_the_old_link_survives(org_and_owner):
    from apps.client_portal.models import MagicLinkToken
    from apps.client_portal.services import generate_magic_link
    from apps.members.models import WorkspaceMembership
    from apps.workspaces.models import Workspace

    org, owner = org_and_owner
    workspace = Workspace.objects.create(organization=org, name="Client")
    client_user = get_user_model().objects.create_user(
        email="client@example.com", password="pw-that-is-long", name="C", tos_accepted_at=timezone.now()
    )
    WorkspaceMembership.objects.create(workspace=workspace, user=client_user, workspace_role="client")
    old = MagicLinkToken.objects.create(user=client_user, workspace=workspace)

    new = generate_magic_link(workspace, client_user, owner)
    run_email_queue()

    assert not MagicLinkToken.objects.filter(pk=new.pk).exists()
    assert MagicLinkToken.objects.get(pk=old.pk).expires_at > timezone.now()


@pytest.mark.django_db
def test_only_the_worker_side_code_sends_mail():
    """The rule must not erode: a request handler that calls ``msg.send()`` again waits for the server.

    Sending belongs to ``apps.common.outbox`` and to the notification engine, which runs only from the
    worker (the delivery task, its retry sweep and the digest sweep). Everything else queues.
    """
    import pathlib
    import re

    import apps

    root = pathlib.Path(apps.__file__).parent
    allowed = {root / "common" / "outbox.py", root / "notifications" / "engine.py", root / "common" / "mail.py"}
    pattern = re.compile(r"send_or_raise\(|\bmsg\.send\(|\.send_messages\(|\bsend_mail\(\s*\"")
    offenders = []
    for path in root.rglob("*.py"):
        if path in allowed or "tests" in path.parts or path.name.startswith("test_") or path.name == "testing.py":
            continue
        if pattern.search(path.read_text()):
            offenders.append(str(path.relative_to(root)))
    assert offenders == []
