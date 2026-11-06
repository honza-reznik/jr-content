"""Housekeeping for the shared tables in this app."""

import logging

from background_task import background

logger = logging.getLogger(__name__)

# Daily is often enough: the rows are tiny and only a week of them is kept.
COUNTER_PURGE_INTERVAL_SECONDS = 24 * 60 * 60
OUTBOX_SWEEP_INTERVAL_SECONDS = 60


@background(schedule=0)
def deliver_outgoing_email(email_id):
    """Send one queued email (``apps.common.outbox``).

    Never raises: django-background-tasks would back a raising task off
    ``attempts ** 4 + 5`` seconds and delete it after 25 attempts, while the outbox keeps
    its own retry state (backoff, attempt cap) on the row.
    """
    from .outbox import deliver

    try:
        deliver(email_id)
    except Exception:
        logger.exception("Delivering outgoing email %s failed", email_id)


@background(schedule=0)
def sweep_outgoing_emails():
    """Re-queue overdue outgoing emails (a worker died mid-send); drop old finished rows."""
    from .outbox import sweep

    try:
        count = sweep()
    except Exception:
        logger.exception("Outgoing email sweep failed")
        return
    if count:
        logger.info("Re-queued %d overdue outgoing email(s)", count)


@background(schedule=0)
def purge_email_counters():
    """Drop email budget buckets whose period is long past.

    One row per recipient per hour accumulates quickly and nothing else ever
    deletes them. Exceptions are swallowed deliberately: django-background-tasks
    backs a raising task off exponentially and deletes it after 25 attempts, and
    losing the schedule over a housekeeping failure is worse than the rows.
    """
    from .mail import purge_expired_counters

    try:
        deleted = purge_expired_counters()
    except Exception:
        logger.exception("Email counter purge failed")
        return

    if deleted:
        logger.info("Purged %d expired email counter row(s)", deleted)
