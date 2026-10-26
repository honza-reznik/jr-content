import re

import pytest
from django.core import mail
from django.test import override_settings
from django.urls import get_script_prefix, set_script_prefix

from apps.notifications.engine import _dispatch_email, _send_digest_email
from apps.notifications.models import EventType, Notification, NotificationDelivery


@pytest.mark.django_db
@pytest.mark.parametrize("prefix", ["", "/jrc"])
def test_all_notification_and_digest_email_links_keep_prefix(user, prefix):
    old = get_script_prefix()
    origin = "https://example.invalid"
    try:
        with override_settings(APP_URL=origin, FORCE_SCRIPT_NAME=prefix or None):
            set_script_prefix(prefix + "/")
            notification = Notification.objects.create(
                user=user,
                event_type=EventType.POST_FAILED,
                title="Test",
                data={"action_url": "/notifications/"},
            )
            delivery = NotificationDelivery(notification=notification, channel="email")
            _dispatch_email(delivery)
            _send_digest_email(user, EventType.POST_FAILED, [notification])
        assert len(mail.outbox) == 2
        for message in mail.outbox:
            for content in [message.body, message.alternatives[0][0]]:
                links = re.findall(r'https://[^\s<>"\']+', content)
                assert origin + prefix + "/notifications/" in links
                assert origin + prefix + "/notifications/preferences/" in links
                assert all(link.startswith(origin + prefix + "/") for link in links)
            assert message.extra_headers["List-Unsubscribe"].startswith(
                "<" + origin + prefix + "/notifications/unsubscribe/"
            )
    finally:
        set_script_prefix(old)
