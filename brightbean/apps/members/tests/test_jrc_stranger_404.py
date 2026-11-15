# JR Content: přidáno 2026-11-15 (blok P2-FABLE), AGPL-3.0.
"""Every JR URL with a workspace in it answers a non-member with 404 (``RBACMiddleware``).

A 403 would confirm that the guessed client exists; the JR pages (``jrc_production``, ``jrc_texts``,
``jrc_thumbnails``) already hid it, the video and channel endpoints answered 403. One rule for all
``jrc_*`` namespaces, checked over every registered route so a new endpoint cannot fall out of it.
"""

import uuid

import pytest
from django.urls import URLPattern, URLResolver, get_resolver
from django.utils import timezone

from apps.accounts.models import User
from apps.members.models import WorkspaceMembership
from apps.workspaces.models import Workspace


def _workspace_routes():
    """``(namespace:name, pattern)`` of every route of a ``jrc_*`` namespace that names a workspace."""
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
                if namespace.startswith("jrc_") and "<uuid:workspace_id>" in route:
                    found.append((f"{namespace}:{entry.name}", route))

    walk(get_resolver().url_patterns, [], "")
    assert len(found) > 20, found
    return found


def _fill(route, workspace):
    def value(match):
        return str(workspace.pk) if match.group(1) == "workspace_id" else str(uuid.uuid4())

    import re

    path = re.sub(r"<uuid:([a-z_]+)>", value, route)
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
def test_every_jrc_workspace_url_is_not_found_for_a_stranger(client, other_workspace, stranger, method):
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
