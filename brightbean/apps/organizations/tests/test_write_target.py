"""P1-B23: a write goes to the organization its page showed, never to the session's current one.

The session's active organization is shared by all browser tabs. These tests use one
Django test client (one session) as two "tabs": tab A renders an organization page,
tab B then opens a workspace of another organization (switching the session), and
tab A submits the form it rendered earlier.
"""

import inspect
import re
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from django.urls import URLPattern, URLResolver, get_resolver, reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.api_keys.models import ApiKey
from apps.members.middleware import ORG_TARGET_FIELD, ORG_TARGET_HEADER
from apps.members.models import Invitation, OrgMembership, WorkspaceMembership
from apps.organizations.models import Organization
from apps.workspaces.models import Workspace

pytestmark = pytest.mark.django_db

HEADER_META_KEY = "HTTP_" + ORG_TARGET_HEADER.upper().replace("-", "_")


def _org(user, name, org_role="owner"):
    org = Organization.objects.create(name=name)
    workspace = Workspace.objects.create(organization=org, name=f"{name} workspace")
    OrgMembership.objects.create(user=user, organization=org, org_role=org_role)
    WorkspaceMembership.objects.create(user=user, workspace=workspace, workspace_role="owner")
    return org, workspace


@pytest.fixture
def two_orgs(client, settings):
    settings.JRC_FF_SYNC_ENABLED = False
    user = User.objects.create_user(email="tabs@example.invalid", tos_accepted_at=timezone.now())
    org_a, ws_a = _org(user, "Org A")
    org_b, ws_b = _org(user, "Org B")
    client.force_login(user)
    return user, org_a, ws_a, org_b, ws_b


def _open_in_tab_a(client, workspace, url):
    """Tab A: open a workspace of A, then the organization page; return its HTML."""
    client.get(reverse("workspaces:settings", args=[workspace.pk]))
    response = client.get(url)
    assert response.status_code == 200
    assert response.wsgi_request.org.pk == workspace.organization_id
    return response.content.decode()


def _switch_in_tab_b(client, workspace):
    """Tab B: open a workspace of the other organization; the shared session follows it."""
    response = client.get(reverse("workspaces:settings", args=[workspace.pk]))
    assert response.status_code == 200
    assert client.session["active_organization_id"] == str(workspace.organization_id)


def _hidden_target(html, action):
    """The organization_id hidden field of the rendered form with this action."""
    forms = re.findall(r"<form\b.*?</form>", html, flags=re.S)
    form = next(f for f in forms if f'name="action" value="{action}"' in f)
    match = re.search(rf'name="{ORG_TARGET_FIELD}" value="([^"]*)"', form)
    assert match, f"form {action} carries no target organization"
    return match.group(1)


def _page_target(html):
    """The organization the page sends with its htmx/fetch requests (base.html)."""
    return re.search(r'data-jrc-organization-id="([^"]*)"', html).group(1)


@pytest.mark.parametrize(
    "action,extra",
    [
        ("update_name", {"name": "Org A renamed"}),
        ("update_timezone", {"timezone": "Europe/Prague"}),
        ("delete_organization", {}),
        ("cancel_deletion", {}),
        ("delete_organization_now", {}),
    ],
)
def test_org_settings_form_from_tab_a_changes_only_a(two_orgs, client, action, extra):
    user, org_a, ws_a, org_b, ws_b = two_orgs
    if action in ("cancel_deletion", "delete_organization_now"):
        # These forms are only shown while a deletion is pending; B is pending too.
        Organization.objects.filter(pk__in=[org_a.pk, org_b.pk]).update(
            deletion_requested_at=timezone.now(), deletion_scheduled_for=timezone.now() + timedelta(days=14)
        )
    html = _open_in_tab_a(client, ws_a, reverse("organizations:settings"))
    target = _hidden_target(html, action)
    assert target == str(org_a.pk)

    _switch_in_tab_b(client, ws_b)
    response = client.post(reverse("organizations:settings"), {"action": action, ORG_TARGET_FIELD: target, **extra})
    assert response.status_code == 302

    org_b.refresh_from_db()
    assert org_b.name == "Org B" and org_b.default_timezone == "UTC" and org_b.deleted_at is None
    if action in ("cancel_deletion", "delete_organization_now"):
        assert org_b.deletion_requested_at is not None
    else:
        assert org_b.deletion_requested_at is None
    if action == "delete_organization_now":
        assert not Organization.objects.filter(pk=org_a.pk).exists()
        assert OrgMembership.objects.filter(user=user, organization=org_b).exists()
        return
    org_a.refresh_from_db()
    assert org_a.name == extra.get("name", "Org A")
    assert org_a.default_timezone == extra.get("timezone", "UTC")
    assert (org_a.deletion_requested_at is not None) == (action == "delete_organization")
    # The redirect after the write shows the organization that was changed.
    assert client.get(reverse("organizations:settings")).wsgi_request.org == org_a


def test_org_write_without_target_is_refused_and_changes_nothing(two_orgs, client):
    _, org_a, ws_a, org_b, ws_b = two_orgs
    _open_in_tab_a(client, ws_a, reverse("organizations:settings"))
    _switch_in_tab_b(client, ws_b)
    for data in (
        {"action": "update_name", "name": "Hijacked"},
        {"action": "delete_organization_now"},
        {"action": "update_name", "name": "Hijacked", ORG_TARGET_FIELD: ""},
        {"action": "update_name", "name": "Hijacked", ORG_TARGET_FIELD: "not-a-uuid"},
    ):
        assert client.post(reverse("organizations:settings"), data).status_code == 403
    assert client.post(reverse("workspaces:create"), {"name": "Stray"}).status_code == 403
    assert client.post(reverse("members:invite"), {"email": "stray@example.invalid"}).status_code == 403
    assert client.post(reverse("api_keys:issue"), {"name": "Stray"}).status_code == 403
    assert set(Organization.objects.values_list("name", flat=True)) == {"Org A", "Org B"}
    assert Workspace.objects.count() == 2
    assert not Invitation.objects.exists() and not ApiKey.objects.exists()


def test_permission_is_checked_against_the_named_target(two_orgs, client):
    user, org_a, ws_a, org_b, ws_b = two_orgs
    OrgMembership.objects.filter(user=user, organization=org_a).update(org_role="member")
    alien = Organization.objects.create(name="Alien")
    deleted = Organization.objects.create(name="Deleted", deleted_at=timezone.now())
    OrgMembership.objects.create(user=user, organization=deleted, org_role="owner")
    _switch_in_tab_b(client, ws_b)  # The session points at B, where the user is owner.
    for target in (org_a, alien, deleted):
        response = client.post(
            reverse("organizations:settings"),
            {"action": "update_name", "name": "Forbidden", ORG_TARGET_FIELD: str(target.pk)},
        )
        assert response.status_code == 403, target.name
    assert set(Organization.objects.values_list("name", flat=True)) == {"Org A", "Org B", "Alien", "Deleted"}
    # A forged target does not change which organization the session shows.
    assert client.session["active_organization_id"] == str(org_b.pk)


def test_workspace_create_goes_to_the_shown_organization(two_orgs, client):
    _, org_a, ws_a, org_b, ws_b = two_orgs
    html = _open_in_tab_a(client, ws_a, reverse("workspaces:list"))
    target = re.search(rf'name="{ORG_TARGET_FIELD}" value="([^"]*)"', html).group(1)
    assert target == str(org_a.pk)
    _switch_in_tab_b(client, ws_b)
    response = client.post(reverse("workspaces:create"), {"name": "New in A", ORG_TARGET_FIELD: target})
    assert response.status_code == 302
    assert Workspace.objects.get(name="New in A").organization == org_a


def test_htmx_member_actions_follow_the_page_organization(two_orgs, client):
    """Team page writes are htmx: the page sends its organization as a header."""
    user, org_a, ws_a, org_b, ws_b = two_orgs
    member_a = OrgMembership.objects.create(
        user=User.objects.create_user(email="member-a@example.invalid"), organization=org_a, org_role="member"
    )
    invite_a = Invitation.objects.create(
        organization=org_a, email="invite-a@example.invalid", expires_at=timezone.now() + timedelta(days=1)
    )
    html = _open_in_tab_a(client, ws_a, reverse("members:list"))
    target = _page_target(html)
    assert target == str(org_a.pk)
    _switch_in_tab_b(client, ws_b)
    headers = {HEADER_META_KEY: target, "HTTP_HX_REQUEST": "true"}

    response = client.post(reverse("members:invite"), {"email": "new-a@example.invalid"}, **headers)
    assert response.status_code == 200
    assert Invitation.objects.get(email="new-a@example.invalid").organization == org_a
    assert client.post(reverse("members:revoke_invite", args=[invite_a.pk]), **headers).status_code in (200, 302)
    invite_a.refresh_from_db()
    assert invite_a.expires_at <= timezone.now() or not Invitation.objects.filter(pk=invite_a.pk).exists()
    response = client.post(reverse("members:update_role", args=[member_a.pk]), {"org_role": "admin"}, **headers)
    assert response.status_code == 200
    member_a.refresh_from_db()
    assert member_a.org_role == "admin"
    # Without the page's target the same htmx write is refused.
    response = client.post(reverse("members:remove", args=[member_a.pk]), HTTP_HX_REQUEST="true")
    assert response.status_code == 403
    assert OrgMembership.objects.filter(pk=member_a.pk).exists()
    assert not Invitation.objects.filter(organization=org_b).exists()


def test_api_key_forms_carry_and_use_the_shown_organization(two_orgs, client):
    user, org_a, ws_a, org_b, ws_b = two_orgs
    key_a = ApiKey.objects.create(
        workspace=ws_a, issued_by=user, name="Key A", lookup_prefix="keyaaa", token_hash="a" * 64
    )
    html = _open_in_tab_a(client, ws_a, reverse("api_keys:list"))
    for action in (reverse("api_keys:issue"), reverse("api_keys:revoke", args=[key_a.pk])):
        form = next(f for f in re.findall(r"<form\b.*?</form>", html, flags=re.S) if f'action="{action}"' in f)
        assert f'name="{ORG_TARGET_FIELD}" value="{org_a.pk}"' in form
    _switch_in_tab_b(client, ws_b)
    response = client.post(reverse("api_keys:revoke", args=[key_a.pk]), {ORG_TARGET_FIELD: str(org_a.pk)})
    assert response.status_code == 302
    key_a.refresh_from_db()
    assert key_a.revoked_at is not None


# Global URLs (no workspace or organization in the path) whose views read the
# session-derived request.org/request.workspace. Each one is classified here;
# a new such route makes test_every_global_org_route_is_classified fail until
# it either refuses untargeted writes or is shown to only read/navigate.
ORG_WRITE_ROUTES = {
    "organizations:settings",
    "workspaces:create",
    "members:invite",
    "members:resend_invite",
    "members:revoke_invite",
    "members:update_role",
    "members:remove",
    "members:manage_workspaces",
    "api_keys:list",
    "api_keys:issue",
    "api_keys:revoke",
    "api_keys:edit",
    "api_keys:delete",
    "api_keys:workspace_options",
    "organizations:workspaces",
    "members:list",
    "media_library_org:shared_index",
    "media_library_org:shared_upload",
    "media_library_org:shared_tag_autocomplete",
    "media_library_org:shared_asset_detail",
    "media_library_org:shared_asset_edit",
    "media_library_org:shared_asset_delete",
    "media_library_org:shared_asset_tags",
    "media_library_org:shared_asset_download",
    "media_library_org:shared_version_list",
}
# Read or navigation only, or the write target comes from the request itself:
# the session only decides what to show, where to go next, or holds a nonce.
READ_ONLY_ROUTES = {
    "dashboard",  # picks the workspace to open; only clears a stale pointer
    "accounts:settings",  # personal account; org role is displayed only
    "workspaces:list",
    "organizations:cross_workspace_calendar",
    "organizations:switch",  # target is the posted organization, checked itself
    "members:accept_invite",  # target comes from the invitation token
    "social_accounts:oauth_callback",  # target comes from the signed OAuth state
    # Target is the OAuth flow named in the URL/form; its workspace access is
    # checked again before every write (test_account_picker_* below).
    "social_accounts:select_account",
    "jrc_production:quick_idea",  # posts its workspace, checked; session only remembers the choice
    # Connection links: the link token is in the URL / signed OAuth state;
    # the session holds the nonce and a flash message.
    "onboarding:connection_page",
    "onboarding:connection_oauth_start",
    "onboarding:connection_bluesky",
    "onboarding:connection_mastodon",
    "onboarding:oauth_callback",
    "account_reset_password_from_key",  # allauth reset key in the URL; no organization or workspace
}


def _global_routes():
    def walk(patterns, prefix="", namespace=""):
        for pattern in patterns:
            if isinstance(pattern, URLResolver):
                ns = f"{namespace}{pattern.namespace}:" if pattern.namespace else namespace
                yield from walk(pattern.url_patterns, prefix + str(pattern.pattern), ns)
            elif isinstance(pattern, URLPattern) and pattern.name:
                yield prefix + str(pattern.pattern), f"{namespace}{pattern.name}", pattern.callback

    for route, name, callback in walk(get_resolver().url_patterns):
        if "workspace_id" in route or "org_id" in route:
            continue
        view = inspect.unwrap(callback)
        try:
            source = inspect.getsource(view)
        except (OSError, TypeError):
            continue
        if re.search(r"request\.(org|workspace|session)\b|last_workspace_id", source):
            yield route, name


def test_every_global_org_route_is_classified():
    found = {name for _, name in _global_routes()}
    assert found - ORG_WRITE_ROUTES - READ_ONLY_ROUTES == set()


def test_untargeted_post_to_any_org_route_is_refused(two_orgs, client):
    """Every org route refuses a POST that does not name its organization."""
    _, org_a, ws_a, org_b, ws_b = two_orgs
    _switch_in_tab_b(client, ws_b)
    for route, name in _global_routes():
        if name not in ORG_WRITE_ROUTES:
            continue
        kwargs = {}
        for param in re.findall(r"<(?:\w+:)?(\w+)>", route):
            kwargs[param] = uuid4()
        response = client.post(reverse(name, kwargs=kwargs), {"name": "Stray", "action": "update_name"})
        assert response.status_code in (403, 405), (name, response.status_code)
    assert set(Organization.objects.values_list("name", flat=True)) == {"Org A", "Org B"}


# Every session key the application reads or writes, with why it is not a
# write target taken from the shared session. The session is common to all
# tabs (and, for the portal, all magic links), so a write target must come
# from the request itself; a new key fails test_every_session_key_is_classified
# until it is shown to be safe here.
SESSION_KEYS = {
    '"active_organization_id"': "display selection only; writes are bound to the organization the page names",
    '"jrc_board_client"': "board filter (display)",
    '"jrc_board_idea_client"': "preselected client in the idea form; the form posts its workspace, checked against membership",
    '"connection_link_error"': "flash message for the connection-link page",
    '"connection_link_token"': "stored only; the OAuth target comes from the signed state",
    '"connection_link_oauth"': "nonce/PKCE check; target from the signed state and link token",
    '"social_oauth"': "nonce/PKCE check; target workspace from the signed state",
    '"oauth_page_select"': "pending pickers per OAuth flow; form names its flow, access rechecked",
    '"pending_invite_token"': "invitation being accepted; target is the invitation itself",
    '"api_key_pending_reveal"': "one-time token, shown only on its own organization's list with permission",
    '"is_portal_session"': "portal login marker",
    '"portal_workspace_id"': "portal session is bound to one workspace by its magic link; posts are addressed by "
    "id in the URL and only found inside that workspace (a mismatch is 404, never another workspace)",
    'f"pending_media_{workspace.id}"': "pending uploads keyed by the workspace in the URL, filtered by it",
    'f"pending_media_{membership.workspace_id}"': "same pending uploads, read by the P1-B25 write gate; the "
    "membership is the one of the workspace in the URL",
    'f"csv_import_{workspace.id}"': "import payload keyed by the workspace in the URL",
    'f"csv_mapping_{workspace.id}"': "import mapping keyed by the workspace in the URL",
    'f"thumbnail_error:{variant.pk}"': "flash message for one variant",
}
SESSION_ACCESS = re.compile(r"session(?:\[|\.get\(|\.pop\(|\.setdefault\()\s*([^\],)]+)")


def _session_keys():
    from django.conf import settings

    apps_dir = Path(settings.BASE_DIR) / "apps"
    for path in apps_dir.rglob("*.py"):
        if {"tests", "migrations"} & set(path.parts) or path.name.startswith("test"):
            continue
        text = path.read_text()
        for match in SESSION_ACCESS.finditer(text):
            expr = match.group(1).strip()
            # Resolve a constant or local variable to the expression it holds.
            assigned = re.fullmatch(r"\w+", expr) and re.search(rf"^\s*{expr}\s*=\s*(.+)$", text, re.M)
            if assigned:
                expr = assigned.group(1).strip()
            yield expr, path.relative_to(apps_dir)


def test_every_session_key_is_classified():
    found = {}
    for expr, path in _session_keys():
        found.setdefault(expr, set()).add(str(path))
    unclassified = {expr: paths for expr, paths in found.items() if expr not in SESSION_KEYS}
    assert unclassified == {}
    assert set(SESSION_KEYS) - set(found) == set(), "classified keys no longer used"


# --- The active organization changes only after an action really succeeded ---


def test_refused_workspace_write_does_not_change_active_organization(two_orgs, client):
    user, org_a, ws_a, org_b, ws_b = two_orgs
    WorkspaceMembership.objects.filter(user=user, workspace=ws_a).update(workspace_role="viewer")
    _switch_in_tab_b(client, ws_b)
    for method in ("get", "post"):
        response = getattr(client, method)(reverse("workspaces:settings", args=[ws_a.pk]), {"name": "Forbidden"})
        assert response.status_code == 403
        assert client.session["active_organization_id"] == str(org_b.pk)
        user.refresh_from_db()
        assert user.last_workspace_id == ws_b.pk
    ws_a.refresh_from_db()
    assert ws_a.name == "Org A workspace"


def test_background_or_write_request_to_a_workspace_does_not_change_active_organization(two_orgs, client):
    user, org_a, ws_a, org_b, ws_b = two_orgs
    _switch_in_tab_b(client, ws_b)
    assert client.get(reverse("workspaces:settings", args=[ws_a.pk]), HTTP_HX_REQUEST="true").status_code == 200
    assert client.post(reverse("workspaces:settings", args=[ws_a.pk]), {"name": "Saved A"}).status_code == 302
    assert client.session["active_organization_id"] == str(org_b.pk)
    # Opening the page itself does switch, once it was shown.
    assert client.get(reverse("workspaces:settings", args=[ws_a.pk])).status_code == 200
    assert client.session["active_organization_id"] == str(org_a.pk)
    user.refresh_from_db()
    assert user.last_workspace_id == ws_a.pk


@pytest.mark.parametrize(
    "data",
    [
        {"action": "update_name", "name": "   "},
        {"action": "update_timezone", "timezone": "Mars/Base"},
        {"action": "unknown"},
    ],
)
def test_invalid_org_settings_form_keeps_active_organization(two_orgs, client, data):
    _, org_a, ws_a, org_b, ws_b = two_orgs
    _open_in_tab_a(client, ws_a, reverse("organizations:settings"))
    _switch_in_tab_b(client, ws_b)
    response = client.post(reverse("organizations:settings"), {**data, ORG_TARGET_FIELD: str(org_a.pk)})
    # The same organization's page is shown again with the error.
    assert response.status_code == 400
    assert response.wsgi_request.org == org_a
    assert _hidden_target(response.content.decode(), "update_name") == str(org_a.pk)
    assert client.session["active_organization_id"] == str(org_b.pk)
    org_a.refresh_from_db()
    assert (org_a.name, org_a.default_timezone) == ("Org A", "UTC")


# --- One-time API token is shown only with its own organization ---


def _issue_in_a(client, user, org_a, ws_a):
    from apps.social_accounts.models import SocialAccount

    account = SocialAccount.objects.create(
        workspace=ws_a,
        platform="linkedin_personal",
        account_platform_id="li-tabs",
        account_name="Tabs",
        connection_status=SocialAccount.ConnectionStatus.CONNECTED,
    )
    response = client.post(
        reverse("api_keys:issue"),
        {
            "name": "Key A",
            "workspace_id": str(ws_a.pk),
            "social_account_ids": [str(account.pk)],
            "permissions": ["create_posts"],
            ORG_TARGET_FIELD: str(org_a.pk),
        },
    )
    assert response.status_code == 302
    key = ApiKey.objects.get(name="Key A")
    return key, client.session["api_key_pending_reveal"]["token"]


def test_issued_token_is_never_shown_in_another_organization(two_orgs, client):
    user, org_a, ws_a, org_b, ws_b = two_orgs
    key, token = _issue_in_a(client, user, org_a, ws_a)
    assert key.workspace.organization == org_a
    _switch_in_tab_b(client, ws_b)  # Another tab moves the session to B first.
    page_b = client.get(reverse("api_keys:list"))
    assert page_b.wsgi_request.org == org_b
    assert token not in page_b.content.decode()
    # Shown exactly once, on A's own list.
    client.get(reverse("workspaces:settings", args=[ws_a.pk]))
    page_a = client.get(reverse("api_keys:list"))
    assert token in page_a.content.decode()
    assert token not in client.get(reverse("api_keys:list")).content.decode()


@pytest.mark.parametrize("change", ["membership_removed", "role_lowered", "revoked"])
def test_pending_token_is_dropped_when_it_may_no_longer_be_shown(two_orgs, client, change):
    user, org_a, ws_a, org_b, ws_b = two_orgs
    key, token = _issue_in_a(client, user, org_a, ws_a)
    if change == "membership_removed":
        OrgMembership.objects.filter(user=user, organization=org_a).delete()
    elif change == "role_lowered":
        OrgMembership.objects.filter(user=user, organization=org_a).update(org_role="member")
    else:
        key.revoked_at = timezone.now()
        key.save(update_fields=["revoked_at"])
    _switch_in_tab_b(client, ws_b)
    assert token not in client.get(reverse("api_keys:list")).content.decode()
    assert "api_key_pending_reveal" not in client.session


# --- Account picker after OAuth is bound to its own flow ---


def _pending_flows(client, ws_a, ws_b):
    page = {"id": "page-1", "name": "Shared Page", "access_token": "page-token", "can_publish": True}
    session = client.session
    session["oauth_page_select"] = {
        flow: {
            "workspace_id": str(ws.pk),
            "platform": "facebook",
            "user_tokens": {"access_token": "user-token", "refresh_token": None},
            "pages": [page],
        }
        for flow, ws in (("flow-a", ws_a), ("flow-b", ws_b))
    }
    session.save()


def test_account_picker_writes_into_its_own_flow_workspace(two_orgs, client):
    from apps.social_accounts.models import SocialAccount

    _, org_a, ws_a, org_b, ws_b = two_orgs
    _pending_flows(client, ws_a, ws_b)
    html = client.get(reverse("social_accounts:select_account") + "?flow=flow-a").content.decode()
    assert 'name="flow" value="flow-a"' in html
    response = client.post(reverse("social_accounts:select_account"), {"flow": "flow-a", "selected_pages": ["page-1"]})
    assert response.status_code == 302
    assert list(SocialAccount.objects.values_list("workspace_id", flat=True)) == [ws_a.pk]
    # Flow B is still pending, flow A is used up.
    assert set(client.session["oauth_page_select"]) == {"flow-b"}
    response = client.post(reverse("social_accounts:select_account"), {"flow": "flow-a", "selected_pages": ["page-1"]})
    assert response.status_code == 302 and SocialAccount.objects.count() == 1
    # Without naming a flow nothing is written.
    client.post(reverse("social_accounts:select_account"), {"selected_pages": ["page-1"]})
    assert SocialAccount.objects.count() == 1


@pytest.mark.parametrize("change", ["membership_removed", "role_lowered", "archived"])
def test_account_picker_rechecks_access_before_writing(two_orgs, client, change):
    from apps.social_accounts.models import SocialAccount

    user, org_a, ws_a, org_b, ws_b = two_orgs
    _pending_flows(client, ws_a, ws_b)
    if change == "membership_removed":
        WorkspaceMembership.objects.filter(user=user, workspace=ws_a).delete()
    elif change == "role_lowered":
        WorkspaceMembership.objects.filter(user=user, workspace=ws_a).update(workspace_role="viewer")
    else:
        Workspace.objects.filter(pk=ws_a.pk).update(is_archived=True)
    response = client.post(reverse("social_accounts:select_account"), {"flow": "flow-a", "selected_pages": ["page-1"]})
    assert response.status_code == 403
    assert not SocialAccount.objects.exists()
    assert set(client.session["oauth_page_select"]) == {"flow-b"}
