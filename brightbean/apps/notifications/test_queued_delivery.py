"""P1-B33: notify() never talks to a mail server; the worker delivers, retries, and never doubles."""

import time
from datetime import timedelta

import pytest
from background_task.models import Task
from django.core import mail
from django.test import override_settings
from django.utils import timezone

from apps.common.testing import run_email_queue
from apps.common.tests.test_outbox import FLAKY, SLOW, SLOW_SECONDS, FlakyBackend, budgeted
from apps.notifications import engine
from apps.notifications.engine import notify
from apps.notifications.models import Channel, DeliveryStatus, EventType, NotificationDelivery

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture(autouse=True)
def _clean():
    Task.objects.all().delete()
    FlakyBackend.failures = 0


def email_delivery(notification):
    return NotificationDelivery.objects.get(notification=notification, channel=Channel.EMAIL)


@budgeted(inner=SLOW)
def test_notify_returns_at_once_and_the_worker_sends_the_email(user):
    started = time.monotonic()
    notification = notify(user, EventType.POST_REJECTED, "Post rejected", "Because.")
    assert time.monotonic() - started < SLOW_SECONDS / 2

    delivery = email_delivery(notification)
    assert delivery.status == DeliveryStatus.PENDING and mail.outbox == []
    # The in-app copy is there immediately.
    assert NotificationDelivery.objects.get(notification=notification, channel=Channel.IN_APP).status == "delivered"

    run_email_queue()
    delivery.refresh_from_db()
    assert delivery.status == DeliveryStatus.DELIVERED and delivery.attempts == 1
    assert [m.to for m in mail.outbox] == [[user.email]]


@budgeted()
def test_a_rolled_back_transaction_sends_no_notification_email(user):
    from django.db import transaction

    with pytest.raises(RuntimeError), transaction.atomic():
        notify(user, EventType.POST_REJECTED, "Post rejected", "Because.")
        raise RuntimeError("request failed")

    run_email_queue()
    assert mail.outbox == [] and NotificationDelivery.objects.count() == 0


@budgeted(inner=FLAKY)
def test_a_transient_failure_is_retried_by_the_sweep_and_sent_once(user):
    FlakyBackend.failures = 1
    notification = notify(user, EventType.POST_REJECTED, "Post rejected", "Because.")

    run_email_queue()
    delivery = email_delivery(notification)
    assert delivery.status == DeliveryStatus.PENDING and delivery.attempts == 1
    assert delivery.next_retry_at > timezone.now() and mail.outbox == []

    NotificationDelivery.objects.filter(pk=delivery.pk).update(next_retry_at=timezone.now() - timedelta(seconds=1))
    assert engine.retry_failed_deliveries() == 1
    delivery.refresh_from_db()
    assert delivery.status == DeliveryStatus.DELIVERED and delivery.attempts == 2
    assert len(mail.outbox) == 1

    assert engine.retry_failed_deliveries() == 0
    run_email_queue()
    assert len(mail.outbox) == 1


@budgeted()
def test_the_task_and_the_sweep_never_send_the_same_delivery_twice(user):
    notification = notify(user, EventType.POST_REJECTED, "Post rejected", "Because.")
    delivery = email_delivery(notification)
    # Both read the row before either has claimed it.
    first = NotificationDelivery.objects.select_related("notification", "notification__user").get(pk=delivery.pk)
    second = NotificationDelivery.objects.select_related("notification", "notification__user").get(pk=delivery.pk)

    engine._dispatch(first)
    engine._dispatch(second)

    assert len(mail.outbox) == 1
    assert NotificationDelivery.objects.get(pk=delivery.pk).attempts == 1


@budgeted()
def test_a_lost_task_is_covered_by_the_sweep_after_the_grace_period(user):
    notification = notify(user, EventType.POST_REJECTED, "Post rejected", "Because.")
    Task.objects.all().delete()
    delivery = email_delivery(notification)

    assert engine.retry_failed_deliveries() == 0, "within the grace period the task is still expected"
    NotificationDelivery.objects.filter(pk=delivery.pk).update(next_retry_at=timezone.now() - timedelta(seconds=1))
    assert engine.retry_failed_deliveries() == 1
    assert len(mail.outbox) == 1


@budgeted(EMAIL_SENDING_ENABLED=False)
def test_a_declined_message_is_failed_not_retried(user):
    notification = notify(user, EventType.POST_REJECTED, "Post rejected", "Because.")
    run_email_queue()
    delivery = email_delivery(notification)
    assert delivery.status == DeliveryStatus.FAILED and delivery.next_retry_at is None
    assert engine.retry_failed_deliveries() == 0


@override_settings(NOTIFICATION_WEBHOOKS_ENABLED=True)
@budgeted()
def test_a_webhook_delivery_is_queued_too(user):
    from apps.notifications.models import NotificationPreference

    NotificationPreference.objects.create(
        user=user, event_type=EventType.POST_REJECTED, channel=Channel.WEBHOOK, is_enabled=True
    )
    notification = notify(user, EventType.POST_REJECTED, "Post rejected", "Because.", data={})
    webhook = NotificationDelivery.objects.get(notification=notification, channel=Channel.WEBHOOK)
    assert webhook.status == DeliveryStatus.PENDING and webhook.attempts == 0
    assert Task.objects.filter(task_name="apps.notifications.tasks.dispatch_notification_delivery").count() == 2
