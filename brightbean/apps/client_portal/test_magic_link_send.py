"""A magic link that was not sent must not cost the client the one they have.

The old order revoked every working token, created a new one, then sent — and
swallowed a send failure. A refused address or a spent email budget therefore
locked the client out entirely while the manager was told the link had gone.
"""

from datetime import timedelta
from unittest.mock import patch

import pytest
from django.core import mail
from django.utils import timezone

from apps.accounts.models import User
from apps.client_portal.models import MagicLinkToken
from apps.client_portal.services import generate_magic_link as _generate_magic_link
from apps.common import outbox
from apps.common.mail import EmailNotSentError
from apps.common.models import OutgoingEmail
from apps.common.testing import run_email_queue
from apps.members.models import WorkspaceMembership
from apps.organizations.models import Organization
from apps.workspaces.models import Workspace


@pytest.fixture
def client_setup(db):
    org = Organization.objects.create(name="Org")
    workspace = Workspace.objects.create(organization=org, name="WS")
    manager = User.objects.create_user(
        email="manager@example.com", password="x", name="Manager", tos_accepted_at=timezone.now()
    )
    client_user = User.objects.create_user(
        email="client@example.com", password="x", name="Client", tos_accepted_at=timezone.now()
    )
    WorkspaceMembership.objects.create(
        user=client_user, workspace=workspace, workspace_role=WorkspaceMembership.WorkspaceRole.CLIENT
    )
    return workspace, client_user, manager


def generate_magic_link(**kwargs):
    """Generate the link and let the worker send the email (P1-B33: requests only queue mail)."""
    token = _generate_magic_link(**kwargs)
    run_email_queue()
    return token


def active_tokens(client_user, workspace):
    return MagicLinkToken.objects.filter(
        user=client_user, workspace=workspace, is_consumed=False, expires_at__gt=timezone.now()
    )


@pytest.mark.django_db
def test_a_successful_send_replaces_the_previous_link(client_setup):
    workspace, client_user, manager = client_setup

    first = generate_magic_link(workspace=workspace, client_user=client_user, created_by=manager)
    second = generate_magic_link(workspace=workspace, client_user=client_user, created_by=manager)

    assert len(mail.outbox) == 2
    assert [t.pk for t in active_tokens(client_user, workspace)] == [second.pk]
    first.refresh_from_db()
    assert first.expires_at <= timezone.now()


@pytest.mark.django_db
def test_a_dropped_send_leaves_the_existing_link_working(client_setup):
    """The outbound budget declines by returning 0, not by raising."""
    workspace, client_user, manager = client_setup
    existing = generate_magic_link(workspace=workspace, client_user=client_user, created_by=manager)
    mail.outbox.clear()

    with patch("apps.common.outbox.send_or_raise", side_effect=EmailNotSentError("budget")):
        generate_magic_link(workspace=workspace, client_user=client_user, created_by=manager)

    assert mail.outbox == []
    assert [t.pk for t in active_tokens(client_user, workspace)] == [existing.pk]
    assert MagicLinkToken.objects.count() == 1, "the undelivered token must not be left behind"


@pytest.mark.django_db
def test_a_send_that_keeps_failing_leaves_the_existing_link_working(client_setup):
    workspace, client_user, manager = client_setup
    existing = generate_magic_link(workspace=workspace, client_user=client_user, created_by=manager)
    mail.outbox.clear()

    with patch("apps.common.outbox.send_or_raise", side_effect=RuntimeError("smtp down")):
        generate_magic_link(workspace=workspace, client_user=client_user, created_by=manager)
        # Until the worker has given up, both links work: nothing is taken away unsent.
        assert active_tokens(client_user, workspace).count() == 2
        for _ in range(outbox.MAX_ATTEMPTS):
            OutgoingEmail.objects.update(next_attempt_at=timezone.now() - timedelta(seconds=1))
            for pk in list(OutgoingEmail.objects.filter(status="pending").values_list("pk", flat=True)):
                outbox.deliver(str(pk))

    assert [t.pk for t in active_tokens(client_user, workspace)] == [existing.pk]
    assert MagicLinkToken.objects.count() == 1, "the undelivered token must not be left behind"
