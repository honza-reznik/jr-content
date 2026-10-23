import socket

import pytest
from django.conf import settings
from django.test import override_settings

from apps.members.models import WorkspaceMembership
from apps.workspaces.models import Workspace


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("Text services must not access the network")

    monkeypatch.setattr(socket, "create_connection", blocked)
    resolve = socket.getaddrinfo

    def database_only(host, port, *args, **kwargs):
        database = settings.DATABASES["default"]
        if host == database["HOST"] and str(port) == str(database["PORT"]):
            return resolve(host, port, *args, **kwargs)
        return blocked()

    monkeypatch.setattr(socket, "getaddrinfo", database_only)
    monkeypatch.setattr("requests.sessions.Session.request", blocked)
    monkeypatch.setattr("httpx.Client.request", blocked)
    monkeypatch.setattr("httpx.AsyncClient.request", blocked)
    with override_settings(JRC_SOCIAL_PROVIDERS_ENABLED=False):
        yield


@pytest.fixture
def workspace(organization, user):
    workspace = Workspace.objects.create(organization=organization, name="Texts test")
    WorkspaceMembership.objects.create(workspace=workspace, user=user, workspace_role="owner")
    return workspace
