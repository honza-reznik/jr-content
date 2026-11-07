"""The one rule for sending email from a request: queue it, never send it (P1-B33).

A request that talks to a mail server waits for it. A slow or unreachable server made
"Request changes" take 51 s and an invitation 17 s, and the delay differed between an
existing and an unknown account (a way to learn which addresses have an account). So no
request sends mail any more. ``queue_email`` writes an ``OutgoingEmail`` row and the task
that delivers it **in the caller's transaction**:

* the worker can only see the task after the transaction commits, so an email never
  leaves for data that was rolled back, and never goes missing for data that was kept;
* both are rows in the database, so a restart of the web or the worker loses nothing;
* ``deliver`` retries a transient failure with a backoff and gives up after
  ``MAX_ATTEMPTS``; a message the outbound budget declines (``EmailNotSentError``) is final;
* an atomic claim (a lease on ``next_attempt_at``) lets the task, a retry and the periodic
  sweep all run without ever sending the same row twice at the same time.

An email that depends on the outcome (an invitation's send slot, a client's new login link)
passes ``on_success`` / ``on_failure``: dotted ``module:function`` names called with
``callback_args`` as keyword arguments, in the worker. Closing the row and the callback are
**one transaction**: both happen or neither does. When the callback raises, the row stays
pending with ``finishing`` set, and the retry repeats only that step (never the sending), so
the callback takes effect exactly once.

A pending row is never kept longer than ``MAX_PENDING_AGE`` (the body holds a one-time link):
after that it fails without being sent and the body is erased. The table is also left out of
the backups (``scripts/backup/backup.sh``).

The delivery is at-least-once: a worker killed between the server accepting the message and
the row being marked sent sends it again after the lease. That window is a few milliseconds
and the alternative (losing mail) is worse.

Notifications keep their own delivery record (``NotificationDelivery``) but follow the same
rule; see ``apps.notifications.engine.queue_delivery``.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from django.core.mail import EmailMultiAlternatives
from django.db import transaction
from django.db.models import F
from django.utils import timezone
from django.utils.module_loading import import_string

from .mail import EmailNotSentError, send_or_raise
from .models import OutgoingEmail

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 5
# Wait before attempt 2, 3, 4, 5.
RETRY_BACKOFF = [timedelta(seconds=30), timedelta(minutes=2), timedelta(minutes=10), timedelta(hours=1)]
# How long a claimed row is untouchable; far above the SMTP timeout (20 s).
LEASE = timedelta(minutes=3)
# A row that is finished keeps its metadata this long.
KEEP_FINISHED = timedelta(days=14)
# A pending row older than this is not sent any more: the retries span about 70 minutes, so
# this only catches a worker that was down; a reset link that old is of no use anyway.
MAX_PENDING_AGE = timedelta(hours=6)
# Extra tries for the "record the outcome and run the callback" step once sending is done.
MAX_FINISH_ATTEMPTS = MAX_ATTEMPTS + 5
SWEEP_BATCH = 100
CALLBACK_PREFIX = "apps."


def _task():
    from .tasks import deliver_outgoing_email

    return deliver_outgoing_email


def queue_email(msg, *, on_success: str = "", on_failure: str = "", callback_args: dict | None = None) -> OutgoingEmail:
    """Store ``msg`` for the worker. Call it where ``msg.send()`` used to be.

    Returns at once (two inserts). Inside a transaction the email commits or rolls back
    with it. Raises ``ValueError`` for what the outbox cannot carry (attachments).
    """
    if getattr(msg, "attachments", None):
        raise ValueError("The email outbox does not carry attachments.")
    for name in (on_success, on_failure):
        if name and not name.startswith(CALLBACK_PREFIX):
            raise ValueError(f"Callback {name!r} must live in the application.")
    headers = dict(msg.extra_headers or {})
    row = OutgoingEmail.objects.create(
        next_attempt_at=timezone.now(),
        subject=msg.subject,
        from_email=msg.from_email or "",
        to=list(msg.to),
        cc=list(msg.cc),
        bcc=list(msg.bcc),
        reply_to=list(msg.reply_to),
        body_text=msg.body,
        content_subtype=msg.content_subtype,
        alternatives=[[content, mimetype] for content, mimetype in getattr(msg, "alternatives", [])],
        headers=headers,
        on_success=on_success,
        on_failure=on_failure,
        callback_args=callback_args or {},
    )
    _task()(str(row.pk))
    return row


def _build(row: OutgoingEmail) -> EmailMultiAlternatives:
    msg = EmailMultiAlternatives(
        subject=row.subject,
        body=row.body_text,
        from_email=row.from_email or None,
        to=row.to,
        cc=row.cc,
        bcc=row.bcc,
        reply_to=row.reply_to,
        headers=dict(row.headers),
    )
    msg.content_subtype = row.content_subtype
    for content, mimetype in row.alternatives:
        msg.attach_alternative(content, mimetype)
    return msg


def _run_callback(row: OutgoingEmail, name: str) -> None:
    """Call the callback; an error propagates so the surrounding transaction is undone."""
    if not name:
        return
    import_string(name.replace(":", "."))(**row.callback_args)


def _finish(row: OutgoingEmail, status: str, error: str = "") -> bool:
    """Close the row once. The conditional update is what makes the callback run once."""
    updated = OutgoingEmail.objects.filter(pk=row.pk, status=OutgoingEmail.Status.PENDING).update(
        status=status,
        finishing="",
        last_error=error[:200],
        sent_at=timezone.now() if status == OutgoingEmail.Status.SENT else None,
        # One-time links must not outlive the delivery.
        body_text="",
        alternatives=[],
        headers={},
    )
    return bool(updated)


def _backoff(attempts: int) -> timedelta:
    return RETRY_BACKOFF[min(max(attempts, 1) - 1, len(RETRY_BACKOFF) - 1)]


def _complete(row: OutgoingEmail, status: str, error: str = "") -> bool:
    """Close the row and run its callback in ONE transaction.

    Returns True when done. If the callback fails, the row is not closed: it keeps the known
    outcome in ``finishing`` and is picked up again later to repeat only this step. After
    ``MAX_FINISH_ATTEMPTS`` the row is closed without the callback (logged), so the body is
    erased and the row cannot loop forever.
    """
    name = row.on_success if status == OutgoingEmail.Status.SENT else row.on_failure
    give_up = row.attempts >= MAX_FINISH_ATTEMPTS
    try:
        with transaction.atomic():
            if _finish(row, status, error) and not give_up:
                _run_callback(row, name)
        if give_up and name:
            logger.error("Outbox callback %s for email %s gave up after %d attempts", name, row.pk, row.attempts)
        return True
    except Exception:
        logger.exception("Outbox callback %s for email %s failed; will repeat only that step", name, row.pk)
    delay = _backoff(row.attempts)
    OutgoingEmail.objects.filter(pk=row.pk, status=OutgoingEmail.Status.PENDING).update(
        finishing=status, last_error=error[:200], next_attempt_at=timezone.now() + delay
    )
    _task()(str(row.pk), schedule=delay)
    return False


def deliver(email_id: str) -> str:
    """Send one queued email. Returns ``sent``, ``failed``, ``retry`` or ``skipped``.

    Safe to call from several places at once: only the caller that wins the claim sends.
    """
    now = timezone.now()
    claimed = OutgoingEmail.objects.filter(
        pk=email_id, status=OutgoingEmail.Status.PENDING, next_attempt_at__lte=now
    ).update(attempts=F("attempts") + 1, next_attempt_at=now + LEASE)
    if not claimed:
        return "skipped"
    row = OutgoingEmail.objects.get(pk=email_id)

    if row.finishing:
        # The mail already went out (or was finally refused); only the bookkeeping is left.
        outcome = row.finishing
        return outcome if _complete(row, outcome, row.last_error) else "retry"

    if row.created_at < now - MAX_PENDING_AGE:
        # Too old to be of use and the body still holds a one-time link: do not send, erase it.
        logger.warning("Outgoing email %s waited longer than %s; not sent", row.pk, MAX_PENDING_AGE)
        _complete(row, OutgoingEmail.Status.FAILED, "not sent: waited too long")
        return "failed"

    try:
        send_or_raise(_build(row))
    except EmailNotSentError as exc:
        # Declined on purpose (budget, suppressed address, sending switched off): final.
        logger.info("Outgoing email %s not sent: %s", row.pk, exc)
        _complete(row, OutgoingEmail.Status.FAILED, "not sent: declined by the outbound budget")
        return "failed"
    except Exception as exc:
        # The backend already reduced the error to a fixed category; nothing else is stored.
        reason = str(exc) if str(exc).startswith("SMTP delivery failed") else "mail backend error"
        logger.warning("Outgoing email %s attempt %d failed: %s", row.pk, row.attempts, reason)
        if row.attempts >= MAX_ATTEMPTS:
            _complete(row, OutgoingEmail.Status.FAILED, reason)
            return "failed"
        delay = _backoff(row.attempts)
        OutgoingEmail.objects.filter(pk=row.pk, status=OutgoingEmail.Status.PENDING).update(
            next_attempt_at=timezone.now() + delay, last_error=reason[:200]
        )
        _task()(str(row.pk), schedule=delay)
        return "retry"

    _complete(row, OutgoingEmail.Status.SENT)
    return "sent"


def cancel_pending(**callback_args) -> int:
    """Cancel pending emails whose ``callback_args`` match; their body is erased, no callback runs.

    For a message that has become obsolete before it was sent, e.g. an invitation whose token was
    replaced by a resend: the older email would carry a link that no longer works.
    """
    filters = {f"callback_args__{key}": value for key, value in callback_args.items()}
    return OutgoingEmail.objects.filter(status=OutgoingEmail.Status.PENDING, **filters).update(
        status=OutgoingEmail.Status.FAILED,
        finishing="",
        last_error="cancelled: superseded",
        body_text="",
        alternatives=[],
        headers={},
    )


def sweep() -> int:
    """Hand overdue rows back to the worker; forget old finished ones.

    Catches what a task cannot: a worker killed mid-send (lease expired), a task row that
    was removed. Returns how many deliveries were scheduled.
    """
    now = timezone.now()
    due = list(
        OutgoingEmail.objects.filter(status=OutgoingEmail.Status.PENDING, next_attempt_at__lte=now)
        .order_by("next_attempt_at")
        .values_list("pk", flat=True)[:SWEEP_BATCH]
    )
    with transaction.atomic():
        for pk in due:
            _task()(str(pk))
    OutgoingEmail.objects.filter(
        status__in=[OutgoingEmail.Status.SENT, OutgoingEmail.Status.FAILED],
        created_at__lt=now - KEEP_FINISHED,
    ).delete()
    return len(due)
