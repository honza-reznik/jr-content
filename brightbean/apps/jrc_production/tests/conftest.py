import socket

import pytest
from django.test import override_settings

from apps.workspaces.models import Workspace


@pytest.fixture(autouse=True)
def _social_provider_test_mode():
    with override_settings(JRC_SOCIAL_PROVIDERS_ENABLED=False):
        yield


@pytest.fixture(autouse=True)
def _no_network(db, monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("Production services must not access the network")

    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(socket, "getaddrinfo", blocked)
    monkeypatch.setattr("requests.sessions.Session.request", blocked)
    monkeypatch.setattr("httpx.Client.request", blocked)
    monkeypatch.setattr("httpx.AsyncClient.request", blocked)


@pytest.fixture
def workspace(organization):
    return Workspace.objects.create(organization=organization, name="Production test")
