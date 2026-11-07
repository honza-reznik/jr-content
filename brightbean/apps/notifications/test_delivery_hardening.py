"""P1-B35: a notification delivery is never left without a retry time, never sent past its cap,
and a last attempt that died does not stay pending for ever."""

from datetime import timedelta

import pytest
from background_task.models import Task
from django.core import mail
from django.db import transaction
from django.utils import timezone

from apps.common.testing import run_email_queue
from apps.common.tests.test_outbox import budgeted
from apps.notifications import engine
from apps.notifications.engine import notify
from apps.notifications.models import Channel, DeliveryStatus, EventType, NotificationDelivery

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture(autouse=True)
def _clean():
    Task.objects.all().delete()


def email_delivery(notification):
    return NotificationDelivery.objects.get(notification=notification, channel=Channel.EMAIL)


@budgeted()
def test_every_unbatched_delivery_is_created_with_its_retry_time(user):
    """The INSERT itself carries ``next_retry_at``: no window where the row exists without it."""
    seen = []
    original = NotificationDelivery.objects.create

    def spy(**kwargs):
        seen.append(dict(kwargs))
        return original(**kwargs)

    from unittest.mock import patch

    with patch.object(NotificationDelivery.objects, "create", side_effect=spy):
        notify(user, EventType.POST_REJECTED, "Post rejected", "Because.")

    assert seen, "a delivery was created"
    for kwargs in seen:
        assert kwargs["next_retry_at"] is not None, kwargs["channel"]


@budgeted()
def test_a_lost_task_right_after_the_insert_is_still_picked_up_by_the_sweep(user):
    notification = notify(user, EventType.POST_REJECTED, "Post rejected", "Because.")
    Task.objects.all().delete()  # the process died before/after the task: nothing is left but the row
    delivery = email_delivery(notification)
    assert delivery.next_retry_at is not None

    assert engine.retry_failed_deliveries() == 0, "not yet: the task still has its grace period"
    NotificationDelivery.objects.filter(pk=delivery.pk).update(next_retry_at=timezone.now() - timedelta(seconds=1))
    assert engine.retry_failed_deliveries() == 1
    delivery.refresh_from_db()
    assert delivery.status == DeliveryStatus.DELIVERED and len(mail.outbox) == 1


@budgeted()
def test_a_rolled_back_notify_leaves_no_delivery_to_sweep(user):
    with pytest.raises(RuntimeError), transaction.atomic():
        notify(user, EventType.POST_REJECTED, "Post rejected", "Because.")
        raise RuntimeError("request failed")
    assert NotificationDelivery.objects.count() == 0
    assert engine.retry_failed_deliveries() == 0 and mail.outbox == []


@budgeted()
def test_a_last_attempt_that_died_is_closed_once_its_lease_has_run_out_and_not_sent_again(user):
    notification = notify(user, EventType.POST_REJECTED, "Post rejected", "Because.")
    Task.objects.all().delete()
    delivery = email_delivery(notification)
    # The worker claimed the last attempt (counted it, took the lease) and was killed.
    NotificationDelivery.objects.filter(pk=delivery.pk).update(
        attempts=engine.MAX_RETRY_ATTEMPTS, next_retry_at=timezone.now() + engine.DISPATCH_LEASE
    )
    assert engine.retry_failed_deliveries() == 0
    delivery.refresh_from_db()
    assert delivery.status == DeliveryStatus.PENDING, "still within the lease: may be sending right now"

    NotificationDelivery.objects.filter(pk=delivery.pk).update(next_retry_at=timezone.now() - timedelta(seconds=1))
    assert engine.retry_failed_deliveries() == 1
    delivery.refresh_from_db()
    assert delivery.status == DeliveryStatus.FAILED and delivery.next_retry_at is None
    assert "interrupted" in delivery.error_message
    assert mail.outbox == []
    assert engine.retry_failed_deliveries() == 0


@budgeted()
def test_a_delivery_at_its_cap_is_never_claimed_or_sent(user):
    notification = notify(user, EventType.POST_REJECTED, "Post rejected", "Because.")
    Task.objects.all().delete()
    delivery = email_delivery(notification)
    NotificationDelivery.objects.filter(pk=delivery.pk).update(
        attempts=engine.MAX_RETRY_ATTEMPTS, next_retry_at=timezone.now() - timedelta(seconds=1)
    )
    delivery.refresh_from_db()
    engine._dispatch(delivery)
    assert mail.outbox == []
    delivery.refresh_from_db()
    assert delivery.attempts == engine.MAX_RETRY_ATTEMPTS


@budgeted()
def test_the_task_and_the_sweep_still_send_exactly_once(user):
    notification = notify(user, EventType.POST_REJECTED, "Post rejected", "Because.")
    delivery = email_delivery(notification)
    NotificationDelivery.objects.filter(pk=delivery.pk).update(next_retry_at=timezone.now() - timedelta(seconds=1))
    engine.retry_failed_deliveries()
    run_email_queue()
    engine.retry_failed_deliveries()
    assert len(mail.outbox) == 1


@budgeted()
def test_batched_rows_are_not_swept_as_interrupted(user):
    """Digest rows have their own queue (``batch_queued_at``) and their own exhaustion handling."""
    notification = notify(user, EventType.POST_FAILED, "Post failed", "Because.")
    delivery = email_delivery(notification)
    assert delivery.batch_queued_at is not None and delivery.next_retry_at is None
    NotificationDelivery.objects.filter(pk=delivery.pk).update(attempts=engine.MAX_RETRY_ATTEMPTS)
    engine.retry_failed_deliveries()
    delivery.refresh_from_db()
    assert delivery.status == DeliveryStatus.PENDING
