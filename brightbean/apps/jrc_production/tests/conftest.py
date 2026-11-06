import socket

import pytest
from django.db import connection
from django.test import override_settings

from apps.members.models import WorkspaceMembership
from apps.workspaces.models import Workspace


@pytest.fixture(autouse=True)
def _social_provider_test_mode():
    with override_settings(JRC_SOCIAL_PROVIDERS_ENABLED=False):
        yield


@pytest.fixture(autouse=True)
def _no_network(db, monkeypatch):
    # Transactional tests can close the previous test's DB connection. Resolve
    # PostgreSQL before blocking DNS for application code and external clients.
    connection.ensure_connection()

    def blocked(*args, **kwargs):
        raise AssertionError("Production services must not access the network")

    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(socket, "getaddrinfo", blocked)
    monkeypatch.setattr("requests.sessions.Session.request", blocked)
    monkeypatch.setattr("httpx.Client.request", blocked)
    monkeypatch.setattr("httpx.AsyncClient.request", blocked)


@pytest.fixture
def workspace(organization, user):
    # P1-B32: the production flow includes Review, which "No approvals" does not have; "Optional approvals"
    # behaves like "No approvals" otherwise (drafts may go out). Mode-specific tests set their own mode.
    workspace = Workspace.objects.create(
        organization=organization, name="Production test", approval_workflow_mode="optional"
    )
    WorkspaceMembership.objects.create(workspace=workspace, user=user, workspace_role="owner")
    return workspace
