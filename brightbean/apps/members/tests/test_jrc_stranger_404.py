# JR Content: přidáno 2026-11-15 (blok P2-FABLE), AGPL-3.0.
"""Every workspace URL answers a non-member with 404 (RBACMiddleware).

The rule also covers upstream calendar/settings/analytics/composer URLs. Walk all registered
workspace routes so a new endpoint cannot accidentally confirm that a client exists.
"""

import uuid

import pytest
from django.urls import URLPattern, URLResolver, get_resolver, reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.members.models import WorkspaceMembership
from apps.workspaces.models import Workspace


def _workspace_routes():
    """``(namespace:name, pattern)`` of every route that names a workspace."""
    found = []

    def walk(patterns, namespaces, prefix):
        for entry in patterns:
            if isinstance(entry, URLResolver):
                walk(
                    entry.url_patterns,
                    namespaces + ([entry.namespace] if entry.namespace else []),
                    prefix + str(entry.pattern),
                )
            elif isinstance(entry, URLPattern):
                route = prefix + str(entry.pattern)
                namespace = ":".join(namespaces)
                if "<uuid:workspace_id>" in route:
                    found.append((f"{namespace}:{entry.name}", route))

    walk(get_resolver().url_patterns, [], "")
    assert len(found) > 20, found
    return found


def _fill(route, workspace):
    def value(match):
        if match.group(2) == "workspace_id":
            return str(workspace.pk)
        return str(uuid.uuid4()) if match.group(1) == "uuid" else "1"

    import re

    path = re.sub(r"<(uuid|int|str|slug|path):([a-z_]+)>", value, route)
    return "/" + path.lstrip("/")


@pytest.fixture
def other_workspace(organization, user):
    """A client ``user`` is a member of; the stranger below is not."""
    workspace = Workspace.objects.create(organization=organization, name="Someone else's client")
    WorkspaceMembership.objects.create(workspace=workspace, user=user, workspace_role="owner")
    return workspace


@pytest.fixture
def stranger(db):
    return User.objects.create_user(email="stranger@example.test", password="x", tos_accepted_at=timezone.now())


@pytest.mark.django_db
@pytest.mark.parametrize("method", ["get", "post"])
def test_every_workspace_url_is_not_found_for_a_stranger(client, other_workspace, stranger, method):
    client.force_login(stranger)
    for name, route in _workspace_routes():
        response = getattr(client, method)(_fill(route, other_workspace))
        assert response.status_code == 404, (name, method, response.status_code)


@pytest.mark.django_db
def test_member_is_not_hidden_by_the_rule(client, other_workspace, user):
    """The 404 comes from the missing membership, not from the routes themselves."""
    client.force_login(user)
    response = client.get(f"/workspaces/{other_workspace.pk}/profile/")
    assert response.status_code == 200


@pytest.mark.django_db
@pytest.mark.parametrize(
    "route", ["calendar:calendar", "workspaces:settings", "workspaces:approvals_settings", "calendar:queue_list"]
)
@pytest.mark.parametrize("existing", [True, False])
def test_upstream_workspace_pages_hide_strangers(client, other_workspace, user, route, existing):
    workspace = other_workspace
    WorkspaceMembership.objects.filter(user=user, workspace=workspace).delete()
    client.force_login(user)
    response = client.get(reverse(route, kwargs={"workspace_id": workspace.pk if existing else uuid.uuid4()}))
    assert response.status_code == 404
    assert workspace.name.encode() not in response.content


@pytest.mark.django_db
def test_member_with_insufficient_role_still_gets_403(client, other_workspace, user):
    workspace = other_workspace
    WorkspaceMembership.objects.filter(user=user, workspace=workspace).update(workspace_role="viewer")
    client.force_login(user)
    assert client.get(reverse("workspaces:settings", kwargs={"workspace_id": workspace.pk})).status_code == 403
