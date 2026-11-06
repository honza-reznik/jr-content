"""P1-B29: one rule decides which workspaces a person is offered."""

import pytest
from django.utils import timezone

from apps.accounts.models import User
from apps.members.models import OrgMembership, WorkspaceMembership
from apps.members.services import member_workspaces
from apps.organizations.models import Organization
from apps.workspaces.models import Workspace

pytestmark = pytest.mark.django_db


def _user(email):
    return User.objects.create_user(email=email, password="testpass123", name=email, tos_accepted_at=timezone.now())


def test_only_member_non_archived_workspaces_of_the_organization():
    org, other = Organization.objects.create(name="A"), Organization.objects.create(name="B")
    user = _user("mw@example.com")
    OrgMembership.objects.create(user=user, organization=org, org_role="owner")
    mine = Workspace.objects.create(organization=org, name="Mine")
    archived = Workspace.objects.create(organization=org, name="Archived", is_archived=True)
    not_member = Workspace.objects.create(organization=org, name="Not member")
    elsewhere = Workspace.objects.create(organization=other, name="Elsewhere")
    for ws in (mine, archived, elsewhere):
        WorkspaceMembership.objects.create(user=user, workspace=ws, workspace_role="owner")
    result = list(member_workspaces(user, org))
    assert result == [mine]
    assert not_member not in result and list(member_workspaces(user, other)) == [elsewhere]


def test_ordered_by_name_without_duplicates():
    org = Organization.objects.create(name="A")
    user = _user("mw2@example.com")
    for name in ("Zeta", "Alpha", "Mid"):
        WorkspaceMembership.objects.create(
            user=user, workspace=Workspace.objects.create(organization=org, name=name), workspace_role="editor"
        )
    assert [w.name for w in member_workspaces(user, org)] == ["Alpha", "Mid", "Zeta"]


def test_no_user_or_organization_gives_nothing():
    from django.contrib.auth.models import AnonymousUser

    org = Organization.objects.create(name="A")
    Workspace.objects.create(organization=org, name="W")
    assert not member_workspaces(None, org).exists()
    assert not member_workspaces(AnonymousUser(), org).exists()
    assert not member_workspaces(_user("mw3@example.com"), None).exists()


def test_sidebar_context_uses_the_same_rule(rf):
    from apps.common.context_processors import sidebar_context as sidebar

    org = Organization.objects.create(name="A")
    user = _user("mw4@example.com")
    OrgMembership.objects.create(user=user, organization=org, org_role="owner")
    mine = Workspace.objects.create(organization=org, name="Mine")
    Workspace.objects.create(organization=org, name="Not mine")
    WorkspaceMembership.objects.create(user=user, workspace=mine, workspace_role="owner")
    request = rf.get("/")
    request.user, request.org = user, org
    assert sidebar(request)["sidebar_workspaces"] == [mine]
