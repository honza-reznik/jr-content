"""P1-B23: real middleware, org/workspace roles, lifecycle, and reversible data repair."""

import io
from datetime import timedelta
from uuid import uuid4

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import Client, RequestFactory
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.accounts.signals import create_organization_on_signup, provision_organization_and_workspace
from apps.api_keys.models import ApiKey
from apps.composer.models import Post
from apps.jrc_channels.services import create_manual_channel
from apps.jrc_video.models import VideoProject
from apps.members.models import Invitation, OrgMembership, WorkspaceMembership
from apps.organizations.models import Organization
from apps.workspaces.models import Workspace

pytestmark = pytest.mark.django_db
ROLES = ["owner", "manager", "editor", "contributor", "client", "viewer", "nonmember", "org_admin"]


@pytest.fixture
def access(role, client, settings):
    settings.JRC_FF_SYNC_ENABLED = False
    user = User.objects.create_user(email="role@example.invalid", tos_accepted_at=timezone.now())
    org = Organization.objects.create(name="Team")
    workspace = Workspace.objects.create(organization=org, name="Main")
    if role != "nonmember":
        OrgMembership.objects.create(
            user=user,
            organization=org,
            org_role="owner" if role == "owner" else "admin" if role == "org_admin" else "member",
        )
        WorkspaceMembership.objects.create(
            user=user, workspace=workspace, workspace_role="viewer" if role == "org_admin" else role
        )
        user.last_workspace_id = workspace.pk
        user.save(update_fields=["last_workspace_id"])
    target = User.objects.create_user(email="target@example.invalid")
    membership = OrgMembership.objects.create(user=target, organization=org, org_role="member")
    invitation = Invitation.objects.create(
        organization=org, email="invite@example.invalid", expires_at=timezone.now() + timedelta(days=1)
    )
    account = create_manual_channel(workspace, "custom", "Local channel")
    key = ApiKey.objects.create(
        workspace=workspace, issued_by=user, name="Existing", lookup_prefix="abcdef", token_hash="x" * 64
    )
    client.force_login(user)
    return user, org, workspace, membership, invitation, account, key


# Requests use real resources, so lack of privileges must be checked before lookup/mutation.
ORG_ENDPOINTS = [
    ("organizations:settings", "get", {}, {}),
    ("organizations:settings", "post", {}, {"action": "update_name", "name": "Changed"}),
    ("organizations:settings", "post", {}, {"action": "update_timezone", "timezone": "Europe/Prague"}),
    ("organizations:settings", "post", {}, {"action": "delete_organization"}),
    ("organizations:settings", "post", {}, {"action": "delete_organization_now"}),
    ("organizations:settings", "post", {}, {"action": "cancel_deletion"}),
    ("organizations:workspaces", "get", {}, {}),
    ("members:list", "get", {}, {}),
    ("members:invite", "post", {}, {"email": "new@example.invalid"}),
    ("members:resend_invite", "post", {"invitation_id": 4}, {}),
    ("members:revoke_invite", "post", {"invitation_id": 4}, {}),
    ("members:update_role", "post", {"membership_id": 3}, {"org_role": "admin"}),
    ("members:remove", "post", {"membership_id": 3}, {}),
    ("members:manage_workspaces", "get", {"membership_id": 3}, {}),
    ("members:manage_workspaces", "post", {"membership_id": 3}, {}),
    ("api_keys:list", "get", {}, {}),
    ("api_keys:issue", "get", {}, {}),
    ("api_keys:issue", "post", {}, {}),
    ("api_keys:workspace_options", "get", {}, {}),
    ("api_keys:edit", "post", {"key_id": 6}, {}),
    ("api_keys:revoke", "post", {"key_id": 6}, {}),
    ("workspaces:create", "post", {}, {"name": "Unauthorized"}),
]
WS_ENDPOINTS = [
    ("workspaces:settings", "get", {}, {}),
    ("workspaces:settings", "post", {}, {"name": "Unauthorized"}),
    ("workspaces:settings", "post", {}, {"action": "archive_workspace"}),
    ("workspaces:settings", "post", {}, {"action": "unarchive_workspace"}),
    ("workspaces:settings", "post", {}, {"action": "delete_workspace"}),
    ("workspaces:settings", "post", {}, {"delete_icon": "1"}),
    ("workspaces:approvals_settings", "get", {}, {}),
    ("workspaces:approvals_settings", "post", {}, {"approval_workflow_mode": "required_internal"}),
    ("jrc_channels:profile", "get", {}, {}),
    ("jrc_channels:profile", "post", {}, {"name": "Unauthorized", "platform": "custom"}),
    ("jrc_channels:color", "post", {}, {"color": "#154c79"}),
    ("jrc_channels:edit", "post", {"account_id": 5}, {"name": "Unauthorized"}),
    ("jrc_channels:toggle", "post", {"account_id": 5}, {"enabled": "false"}),
    ("jrc_channels:icon", "post", {"account_id": 5}, {"remove_icon": "1"}),
]


@pytest.mark.parametrize("role", ROLES)
@pytest.mark.parametrize("name,method,ids,data", ORG_ENDPOINTS + WS_ENDPOINTS)
def test_role_matrix(role, access, client, name, method, ids, data):
    user, org, workspace, member, invite, account, key = access
    is_ws = (name, method, ids, data) in WS_ENDPOINTS
    allowed = role in ({"owner", "manager"} if is_ws else {"owner", "org_admin"})
    kwargs = {field: access[index].pk for field, index in ids.items()}
    if is_ws:
        kwargs["workspace_id"] = workspace.pk
    if method == "post":
        # Every organization form names the organization it was rendered for.
        data = {**data, "organization_id": str(org.pk)}
    response = getattr(client, method)(reverse(name, kwargs=kwargs), data)
    if allowed:
        if (
            role == "org_admin"
            and name == "organizations:settings"
            and data.get("action") in {"delete_organization", "delete_organization_now", "cancel_deletion"}
        ):
            assert response.status_code == 403
        else:
            expected = 200 if method == "get" else 302
            if name == "api_keys:issue" and method == "get":
                expected = 405
            if name == "members:update_role" and role == "org_admin":
                expected = 422  # An admin cannot promote another member to admin.
            assert response.status_code == expected, (role, name, response.status_code)
    else:
        assert response.status_code == 403, (role, name, response.status_code)
        org.refresh_from_db()
        workspace.refresh_from_db()
        key.refresh_from_db()
        account.refresh_from_db()
        assert org.name == "Team"
        assert workspace.name == "Main" and not workspace.is_archived
        assert workspace.approval_workflow_mode == "none"
        assert key.revoked_at is None and account.jrc_channel.is_enabled
        assert OrgMembership.objects.filter(pk=member.pk).exists()
        assert Invitation.objects.filter(pk=invite.pk).exists()
        assert Workspace.objects.count() == 1
        assert account.account_name == "Local channel"


@pytest.mark.parametrize("role", ["owner", "manager"])
def test_workspace_managers_can_save(role, access, client):
    _, _, workspace, *_ = access
    assert client.post(reverse("workspaces:settings", args=[workspace.pk]), {"name": "Saved"}).status_code == 302
    assert (
        client.post(
            reverse("workspaces:approvals_settings", args=[workspace.pk]),
            {"approval_workflow_mode": "required_internal"},
        ).status_code
        == 302
    )
    workspace.refresh_from_db()
    assert (workspace.name, workspace.approval_workflow_mode) == ("Saved", "required_internal")


@pytest.mark.parametrize("role", ["owner"])
def test_workspace_owner_is_not_organization_owner(role, access, client):
    user, org, workspace, *_ = access
    OrgMembership.objects.filter(user=user, organization=org).update(org_role="member")
    for name in ["organizations:settings", "organizations:workspaces", "members:list", "api_keys:list"]:
        assert client.get(reverse(name)).status_code == 403
        assert client.post(reverse(name), {"action": "update_name", "name": "Forbidden"}).status_code == 403
    assert client.get(reverse("workspaces:settings", args=[workspace.pk])).status_code == 200
    assert client.post(reverse("workspaces:settings", args=[workspace.pk]), {"name": "Saved"}).status_code == 302
    org.refresh_from_db()
    workspace.refresh_from_db()
    assert org.name == "Team" and workspace.name == "Saved"


def test_account_creation_has_no_implicit_organization():
    user = User.objects.create_user(email="new@example.invalid")
    assert not user.org_memberships.exists()
    assert not user.workspace_memberships.exists()
    assert not Organization.objects.exists()


@pytest.mark.parametrize("mode", ["open", "invite", "closed"])
def test_invited_signup_keeps_other_organizations_and_does_not_provision(settings, mode):
    settings.SIGNUP_MODE = mode
    user = User.objects.create_user(email="new@example.invalid")
    other = Organization.objects.create(name="Existing")
    OrgMembership.objects.create(user=user, organization=other, org_role="member")
    org = Organization.objects.create(name="Inviting team")
    workspace = Workspace.objects.create(organization=org, name="Invited workspace")
    invite = Invitation.objects.create(
        organization=org,
        email=user.email,
        workspace_assignments=[{"workspace_id": str(workspace.pk), "role": "client"}],
        expires_at=timezone.now() + timedelta(days=1),
    )
    request = RequestFactory().get("/")
    request.session = {"pending_invite_token": invite.token}
    create_organization_on_signup(None, request, user)
    assert set(user.org_memberships.values_list("organization_id", flat=True)) == {other.pk, org.pk}
    assert not Organization.objects.filter(name="My Organization").exists()
    assert user.workspace_memberships.get().workspace_role == "client"
    assert user.tos_accepted_at


def test_open_standalone_signup_provisions_once(settings):
    settings.SIGNUP_MODE = "open"
    user = User.objects.create_user(email="standalone@example.invalid")
    request = RequestFactory().get("/")
    request.session = {}
    create_organization_on_signup(None, request, user)
    create_organization_on_signup(None, request, user)
    assert user.org_memberships.count() == user.workspace_memberships.count() == 1


def test_context_follows_workspace_and_switch_is_authorized(client):
    user = User.objects.create_user(email="multi@example.invalid", tos_accepted_at=timezone.now())
    provision_organization_and_workspace(user)
    personal = user.org_memberships.get().organization
    org = Organization.objects.create(name="Inviting team")
    workspace = Workspace.objects.create(organization=org, name="Team workspace")
    OrgMembership.objects.create(user=user, organization=org, org_role="member")
    WorkspaceMembership.objects.create(user=user, workspace=workspace, workspace_role="editor")
    client.force_login(user)
    response = client.get(reverse("calendar:calendar", args=[workspace.pk]))
    assert response.wsgi_request.org.pk == org.pk
    response = client.get(reverse("accounts:settings"))
    assert response.wsgi_request.org.pk == org.pk
    assert response.context["org_membership"].organization == org
    assert response.context["org_membership"].org_role == "member"
    assert b"organization-switch" in response.content
    assert b"/members/" not in response.content
    assert client.get(reverse("members:list")).status_code == 403
    response = client.post(reverse("organizations:switch"), {"organization_id": str(personal.pk)})
    assert response.status_code == 302
    assert client.get(reverse("members:list")).wsgi_request.org.pk == personal.pk
    alien = Organization.objects.create(name="Other")
    for value in [str(alien.pk), "bad-id", ""]:
        assert client.post(reverse("organizations:switch"), {"organization_id": value}).status_code == 403
    assert client.get(reverse("organizations:switch")).status_code == 405
    secured = Client(enforce_csrf_checks=True)
    secured.force_login(user)
    assert secured.post(reverse("organizations:switch"), {"organization_id": str(org.pk)}).status_code == 403


def test_workspace_context_never_reuses_foreign_owner_membership(client):
    user = User.objects.create_user(email="legacy@example.invalid", tos_accepted_at=timezone.now())
    provision_organization_and_workspace(user)
    org = Organization.objects.create(name="Workspace-only legacy team")
    workspace = Workspace.objects.create(organization=org, name="Legacy")
    WorkspaceMembership.objects.create(user=user, workspace=workspace, workspace_role="manager")
    client.force_login(user)
    response = client.get(reverse("workspaces:settings", args=[workspace.pk]))
    assert response.status_code == 200
    assert response.wsgi_request.org == org
    assert response.wsgi_request.org_membership is None
    assert client.post(reverse("members:invite"), {"email": "blocked@example.invalid"}).status_code == 403


def repair_fixture():
    user = User.objects.create_user(email="legacy@example.invalid")
    provision_organization_and_workspace(user)
    personal = user.org_memberships.get().organization
    org = Organization.objects.create(name="Team")
    workspace = Workspace.objects.create(organization=org, name="Team workspace")
    WorkspaceMembership.objects.create(user=user, workspace=workspace, workspace_role="editor")
    return user, personal, org, workspace


def test_repair_dry_run_apply_and_restore(tmp_path):
    user, personal, org, _ = repair_fixture()
    journal = tmp_path / "repair.json"
    original_ws = user.last_workspace_id
    call_command("jrc_repair_organizations", stdout=io.StringIO())
    assert Organization.objects.filter(pk=personal.pk).exists()
    assert not OrgMembership.objects.filter(user=user, organization=org).exists()
    call_command("jrc_repair_organizations", apply=True, backup_file=journal, stdout=io.StringIO())
    assert not Organization.objects.filter(pk=personal.pk).exists()
    assert OrgMembership.objects.get(user=user, organization=org).org_role == "member"
    user.refresh_from_db()
    assert user.last_workspace_id is None
    assert journal.stat().st_mode & 0o777 == 0o600
    assert "password" not in journal.read_text()
    call_command("jrc_repair_organizations", restore=journal, stdout=io.StringIO())
    assert Organization.objects.filter(pk=personal.pk).exists()
    assert not OrgMembership.objects.filter(user=user, organization=org).exists()
    user.refresh_from_db()
    assert user.last_workspace_id == original_ws
    with pytest.raises(CommandError, match="Konflikt"):
        call_command("jrc_repair_organizations", restore=journal)


@pytest.mark.parametrize(
    "protected",
    [
        "post",
        "channel",
        "key",
        "invitation",
        "renamed",
        "icon",
        "colour",
        "timezone",
        "video_project",
        "other_member",
        "sole_org",
    ],
)
def test_repair_preserves_any_content_or_customization(tmp_path, protected):
    user, personal, org, _ = repair_fixture()
    workspace = personal.workspaces.get()
    if protected == "post":
        Post.objects.create(workspace=workspace, author=user, caption="Keep")
    elif protected == "channel":
        create_manual_channel(workspace, "custom", "Keep")
    elif protected == "key":
        ApiKey.objects.create(workspace=workspace, name="Keep", lookup_prefix="keep", token_hash="x" * 64)
    elif protected == "invitation":
        Invitation.objects.create(
            organization=personal, email="keep@example.invalid", expires_at=timezone.now() + timedelta(days=1)
        )
    elif protected == "renamed":
        workspace.name = "Renamed"
        workspace.save()
    elif protected == "icon":
        workspace.icon = "icon.png"
        workspace.save()
    elif protected == "colour":
        workspace.primary_color = "#123456"
        workspace.save()
    elif protected == "timezone":
        personal.default_timezone = "Europe/Prague"
        personal.save()
    elif protected == "video_project":
        VideoProject.objects.create(workspace=workspace, ff_project_id=uuid4(), synced_at=timezone.now())
    elif protected == "other_member":
        OrgMembership.objects.create(
            organization=personal, user=User.objects.create_user(email="other@example.invalid")
        )
    else:
        org.delete()
    call_command("jrc_repair_organizations", apply=True, backup_file=tmp_path / "repair.json", stdout=io.StringIO())
    assert Organization.objects.filter(pk=personal.pk).exists()


def test_repair_requires_new_journal_and_refuses_changed_membership(tmp_path):
    user, personal, org, _ = repair_fixture()
    path = tmp_path / "repair.json"
    with pytest.raises(CommandError):
        call_command("jrc_repair_organizations", apply=True)
    path.write_text("existing")
    with pytest.raises(CommandError):
        call_command("jrc_repair_organizations", apply=True, backup_file=path)
    assert Organization.objects.filter(pk=personal.pk).exists()
    path.unlink()
    call_command("jrc_repair_organizations", apply=True, backup_file=path, stdout=io.StringIO())
    OrgMembership.objects.filter(user=user, organization=org).update(org_role="admin")
    with pytest.raises(CommandError, match="Konflikt"):
        call_command("jrc_repair_organizations", restore=path)
    assert not Organization.objects.filter(pk=personal.pk).exists()


def test_repair_restore_preserves_new_workspace_choice(tmp_path):
    user, personal, _, workspace = repair_fixture()
    path = tmp_path / "repair.json"
    call_command("jrc_repair_organizations", apply=True, backup_file=path, stdout=io.StringIO())
    User.objects.filter(pk=user.pk).update(last_workspace_id=workspace.pk)
    call_command("jrc_repair_organizations", restore=path, stdout=io.StringIO())
    user.refresh_from_db()
    assert user.last_workspace_id == workspace.pk
    assert Organization.objects.filter(pk=personal.pk).exists()


def test_deleting_an_org_keeps_other_memberships_without_provisioning():
    user = User.objects.create_user(email="multi-delete@example.invalid")
    org = Organization.objects.create(name="Delete")
    other = Organization.objects.create(name="Keep")
    OrgMembership.objects.create(user=user, organization=org, org_role="owner")
    OrgMembership.objects.create(user=user, organization=other, org_role="owner")
    member = User.objects.create_user(email="invited-delete@example.invalid")
    OrgMembership.objects.create(user=member, organization=org, org_role="member")
    org.hard_delete(requesting_user=user)
    assert User.objects.filter(pk=user.pk).exists()
    assert User.objects.filter(pk=member.pk).exists()
    assert user.org_memberships.get().organization == other
    assert not member.org_memberships.exists()
    assert not Organization.objects.filter(name="My Organization").exists()


@pytest.mark.parametrize("role", ROLES)
def test_navigation_matches_role(role, access, client):
    response = client.get(reverse("accounts:settings"))
    assert response.status_code == 200
    assert (b"/members/" in response.content) == (role in {"owner", "org_admin"})
    assert (b"/organizations/api-keys/" in response.content) == (role in {"owner", "org_admin"})
    assert (b"Workspace settings" in response.content) == (role in {"owner", "manager"})


@pytest.mark.parametrize("role", ROLES)
@pytest.mark.parametrize(
    "name,method,ids,data",
    [
        endpoint
        for endpoint in ORG_ENDPOINTS + WS_ENDPOINTS
        if endpoint[1] == "post"
        and endpoint[0]
        not in {
            "organizations:settings",
            "workspaces:settings",
            "workspaces:approvals_settings",
            "jrc_channels:profile",
            "members:manage_workspaces",
        }
    ],
)
def test_action_get_cannot_mutate(role, access, client, name, method, ids, data):
    _, org, workspace, member, invite, account, key = access
    kwargs = {field: access[index].pk for field, index in ids.items()}
    is_ws = name.startswith("jrc_channels:")
    if is_ws:
        kwargs["workspace_id"] = workspace.pk
    # Middleware rejects a nonmember before the method check on workspace URLs.
    # The method decorators on channel actions and workspace creation run first.
    expected = 405 if is_ws or name == "workspaces:create" or role in {"owner", "org_admin"} else 403
    if is_ws and role == "nonmember":
        expected = 403
    assert client.get(reverse(name, kwargs=kwargs), data).status_code == expected
    assert Organization.objects.filter(pk=org.pk, name="Team").exists()
    assert Workspace.objects.filter(pk=workspace.pk, name="Main", is_archived=False).exists()
    assert OrgMembership.objects.filter(pk=member.pk).exists()
    assert Invitation.objects.filter(pk=invite.pk).exists()
    assert ApiKey.objects.get(pk=key.pk).revoked_at is None
    assert account.jrc_channel.is_enabled


@pytest.mark.parametrize("role", ROLES)
@pytest.mark.parametrize(
    "name", ["members:list", "organizations:workspaces", "api_keys:list", "api_keys:workspace_options"]
)
def test_listing_post_cannot_bypass_org_permissions(role, access, client, name):
    org = access[1]
    expected = (405 if name == "members:list" else 200) if role in {"owner", "org_admin"} else 403
    assert client.post(reverse(name), {"organization_id": str(org.pk)}).status_code == expected
    # Without naming its organization the write is refused for every role.
    assert client.post(reverse(name)).status_code == 403


@pytest.mark.parametrize("role", ["owner", "org_admin"])
def test_inviting_with_foreign_workspace_is_rejected(role, access, client):
    outsider_org = Organization.objects.create(name="Outside")
    outsider_ws = Workspace.objects.create(organization=outsider_org, name="Outside")
    response = client.post(
        reverse("members:invite"),
        {"email": "new@example.invalid", f"ws_{outsider_ws.pk}": "on", "organization_id": str(access[1].pk)},
    )
    assert response.status_code == 422
    assert not Invitation.objects.filter(email="new@example.invalid").exists()


def test_switch_to_organization_without_workspace_and_stale_selection(client):
    user = User.objects.create_user(email="switch@example.invalid", tos_accepted_at=timezone.now())
    first = Organization.objects.create(name="First")
    second = Organization.objects.create(name="Second")
    OrgMembership.objects.create(user=user, organization=first, org_role="owner")
    membership = OrgMembership.objects.create(user=user, organization=second, org_role="member")
    client.force_login(user)
    response = client.post(reverse("organizations:switch"), {"organization_id": second.pk}, follow=True)
    assert response.status_code == 200
    assert response.wsgi_request.org == second
    assert not response.wsgi_request.workspace
    assert client.get(reverse("members:list")).status_code == 403
    membership.delete()
    response = client.get(reverse("accounts:settings"))
    assert response.wsgi_request.org == first
    assert response.context["org_membership"].organization == first
    second.deleted_at = timezone.now()
    second.save()
    OrgMembership.objects.create(user=user, organization=second, org_role="owner")
    assert client.post(reverse("organizations:switch"), {"organization_id": second.pk}).status_code == 403


def test_deleted_organization_cannot_be_selected_through_workspace_url(client):
    user = User.objects.create_user(email="deleted-org@example.invalid", tos_accepted_at=timezone.now())
    provision_organization_and_workspace(user)
    workspace = user.workspace_memberships.get().workspace
    Organization.objects.filter(pk=workspace.organization_id).update(deleted_at=timezone.now())
    client.force_login(user)
    assert client.get(reverse("workspaces:settings", args=[workspace.pk])).status_code == 403
    assert client.post(reverse("workspaces:settings", args=[workspace.pk]), {"name": "Forbidden"}).status_code == 403
    assert client.get(reverse("accounts:settings")).wsgi_request.org is None


def test_deleting_current_organization_preserves_session_and_other_workspace(client):
    user = User.objects.create_user(email="delete-current@example.invalid", tos_accepted_at=timezone.now())
    provision_organization_and_workspace(user)
    personal = user.org_memberships.get().organization
    other = Organization.objects.create(name="Remaining")
    OrgMembership.objects.create(user=user, organization=other, org_role="member")
    workspace = Workspace.objects.create(organization=other, name="Remaining workspace")
    WorkspaceMembership.objects.create(user=user, workspace=workspace, workspace_role="editor")
    client.force_login(user)
    response = client.post(
        reverse("organizations:settings"),
        {"action": "delete_organization_now", "organization_id": str(personal.pk)},
        follow=True,
    )
    assert response.status_code == 200
    assert response.wsgi_request.user.is_authenticated
    assert response.wsgi_request.workspace == workspace
    assert not Organization.objects.filter(pk=personal.pk).exists()
    assert client.get(reverse("members:list")).status_code == 403


@pytest.mark.parametrize(
    "name,id_field",
    [("members:remove", "membership_id"), ("members:revoke_invite", "invitation_id"), ("api_keys:revoke", "key_id")],
)
def test_org_owner_cannot_mutate_foreign_resources(client, name, id_field):
    user = User.objects.create_user(email="scope@example.invalid", tos_accepted_at=timezone.now())
    provision_organization_and_workspace(user)
    foreign = Organization.objects.create(name="Foreign")
    workspace = Workspace.objects.create(organization=foreign, name="Foreign workspace")
    target = User.objects.create_user(email="foreign@example.invalid")
    member = OrgMembership.objects.create(user=target, organization=foreign)
    invite = Invitation.objects.create(
        organization=foreign, email="foreign-invite@example.invalid", expires_at=timezone.now() + timedelta(days=1)
    )
    key = ApiKey.objects.create(workspace=workspace, name="Foreign", lookup_prefix="foreign", token_hash="f" * 64)
    client.force_login(user)
    target_id = {"membership_id": member.pk, "invitation_id": invite.pk, "key_id": key.pk}[id_field]
    own_org = user.org_memberships.get().organization_id
    assert (
        client.post(reverse(name, kwargs={id_field: target_id}), {"organization_id": str(own_org)}).status_code == 404
    )
    assert OrgMembership.objects.filter(pk=member.pk).exists()
    assert Invitation.objects.get(pk=invite.pk).expires_at > timezone.now()
    assert ApiKey.objects.get(pk=key.pk).revoked_at is None


@pytest.mark.parametrize("role", ["manager", "client"])
def test_real_invited_registration_has_only_inviting_organization(client, settings, role):
    settings.SIGNUP_MODE = "invite"
    org = Organization.objects.create(name="Inviting")
    workspace = Workspace.objects.create(organization=org, name="Inviting workspace")
    invitation = Invitation.objects.create(
        organization=org,
        email="signup@example.invalid",
        expires_at=timezone.now() + timedelta(days=1),
        workspace_assignments=[{"workspace_id": str(workspace.pk), "role": role}],
    )
    client.get(reverse("members:accept_invite", args=[invitation.token]))
    response = client.post(
        reverse("account_signup"), {"email": invitation.email, "password1": "Test-invitation-pass-972!"}
    )
    assert response.status_code == 302
    user = User.objects.get(email=invitation.email)
    assert list(user.org_memberships.values_list("organization_id", flat=True)) == [org.pk]
    assert list(user.workspace_memberships.values_list("workspace_id", "workspace_role")) == [(workspace.pk, role)]
    assert Organization.objects.count() == Workspace.objects.count() == 1
