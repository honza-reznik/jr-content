"""Durable bookkeeping for outbound email.

Two tables, both read and written on the send path in ``apps.common.mail``:

``EmailSendCounter``
    The budget. One row per (scope, key, period) — the global daily total, and
    per-recipient hourly/daily totals.

``EmailSuppression``
    Addresses we have been told to stop mailing (hard bounce, spam complaint).

The state lives in the database rather than the cache for the same reason
``apps.common.quota`` gives: ``REDIS_URL`` is optional
(``config/settings/base.py``), so the fallback cache is a per-process
LocMemCache that every deploy and every dyno restart empties — precisely the
moments a budget most needs to hold. A cache-backed cap is also per-process, so
it silently multiplies by the worker count the first time the app scales out.
"""

from __future__ import annotations

import uuid

from django.db import models


class EmailSendCounter(models.Model):
    """One counted bucket of outbound email.

    ``key`` is empty for the global scope and a truncated SHA-256 of the
    lowercased address for the per-recipient scopes, so the table never becomes
    a second copy of the user list. ``period_start`` is the bucket boundary
    (midnight UTC for daily scopes, the top of the hour for hourly ones), which
    is what makes expiry a comparison rather than a cleanup job.
    """

    class Scope(models.TextChoices):
        GLOBAL_DAY = "global_day", "Global (day)"
        RECIPIENT_HOUR = "recipient_hour", "Recipient (hour)"
        RECIPIENT_DAY = "recipient_day", "Recipient (day)"
        INVITE_ORG_DAY = "invite_org_day", "Invitations per organization (day)"

    scope = models.CharField(max_length=20, choices=Scope.choices)
    key = models.CharField(max_length=64, blank=True, default="")
    period_start = models.DateTimeField()
    count = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "common_email_send_counter"
        unique_together = [("scope", "key", "period_start")]

    def __str__(self) -> str:
        return f"{self.scope}:{self.key or '-'} @ {self.period_start.isoformat()} = {self.count}"


class EmailSuppression(models.Model):
    """An address we must stop sending to.

    Populated by the Resend bounce/complaint webhook and by hand through the
    admin. Rows are deleted, not flagged, to release an address — there is no
    state worth keeping for an address that has started accepting mail again.
    """

    class Reason(models.TextChoices):
        BOUNCED = "bounced", "Hard bounce"
        COMPLAINED = "complained", "Spam complaint"
        MANUAL = "manual", "Added by hand"

    address = models.EmailField(unique=True)
    reason = models.CharField(max_length=20, choices=Reason.choices, default=Reason.MANUAL)
    detail = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "common_email_suppression"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.address} ({self.reason})"


class OutgoingEmail(models.Model):
    """One email waiting for the worker to send it (the outbox, P1-B33).

    A request never talks to the mail server: it writes this row (and the task that
    delivers it) in its own transaction, so the email exists exactly when the data it
    is about exists, survives a restart, and is retried by the worker when the server
    is briefly unreachable. ``apps.common.outbox`` owns the lifecycle.

    The bodies hold one-time links (invitation, login, reset), so they are erased as soon
    as the row is finished; only the metadata stays for a while.
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        SENT = "sent", "Sent"
        FAILED = "failed", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    # Doubles as the lease: the worker pushes it forward while it holds the row, so a
    # sweep never starts a second delivery of an email that is being sent right now.
    next_attempt_at = models.DateTimeField(db_index=True)
    attempts = models.PositiveIntegerField(default=0)
    sent_at = models.DateTimeField(null=True, blank=True)
    last_error = models.CharField(max_length=200, blank=True, default="")
    # Set when the outcome is known (the mail went out / was finally refused) but the transaction
    # that records it and runs the callback has not committed yet: the retry then only repeats
    # that step and never sends the message again (P1-B35).
    finishing = models.CharField(max_length=10, blank=True, default="")

    subject = models.CharField(max_length=998)
    from_email = models.CharField(max_length=320, blank=True, default="")
    to = models.JSONField(default=list)
    cc = models.JSONField(default=list, blank=True)
    bcc = models.JSONField(default=list, blank=True)
    reply_to = models.JSONField(default=list, blank=True)
    # Erased when the row is finished (see class docstring).
    body_text = models.TextField(blank=True, default="")
    content_subtype = models.CharField(max_length=10, default="plain")
    alternatives = models.JSONField(default=list, blank=True)
    headers = models.JSONField(default=dict, blank=True)

    # Dotted ``module:function`` names run once when the email has been sent / has
    # definitively not been sent, with ``callback_args`` as keyword arguments.
    on_success = models.CharField(max_length=200, blank=True, default="")
    on_failure = models.CharField(max_length=200, blank=True, default="")
    callback_args = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "common_outgoing_email"
        indexes = [models.Index(fields=["status", "next_attempt_at"])]

    def __str__(self) -> str:
        return f"{self.subject[:60]} ({self.status})"
