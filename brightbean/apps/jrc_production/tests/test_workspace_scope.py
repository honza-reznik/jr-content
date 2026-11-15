# JR Content: přidáno 2026-11-14 (blok P2-B13), AGPL-3.0.
"""P2-B13: one workspace = one client; the admin overview of all clients.

AK1 the board, its filters and the menu show only the current workspace; links to another client
switch it (members) or are refused (others). AK2 new content is always created in the current
workspace and the server refuses any other. AK3 the admin overview (board, calendar, read-only
content summary) is for the organization owner and admins only, also for clients they are not a
member of.
"""

import json
from datetime import timedelta

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.approvals.models import PostComment
from apps.composer.models import PlatformPost, Post
from apps.jrc_channels.services import create_manual_channel
from apps.jrc_production.services import create_content, set_publication_date
from apps.members.models import PERMISSION_KEYS, OrgMembership, WorkspaceMembership
from apps.notifications.models import Notification
from apps.organizations.models import Organization
from apps.workspaces.models import Workspace

pytestmark = pytest.mark.django_db

BOARD = reverse("jrc_production:board")
CREATE = reverse("jrc_production:create")
IDEA = reverse("jrc_production:quick_idea")
NEW = reverse("jrc_production:new_content")
OVERVIEW = reverse("jrc_production:overview_board")
CALENDAR = reverse("organizations:cross_workspace_calendar")
XHR = {"HTTP_X_REQUESTED_WITH": "XMLHttpRequest"}


def make_user(email):
    return User.objects.create_user(
        email=email, password="x-pass-123", name=email.split("@")[0], tos_accepted_at=timezone.now()
    )


def login(user):
    client = Client()
    client.force_login(user)
    return client


def detail_url(post):
    return reverse("jrc_production:content_detail", kwargs={"workspace_id": post.workspace_id, "post_id": post.pk})


def overview_url(post):
    return reverse("jrc_production:overview_content", kwargs={"post_id": post.pk})


@pytest.fixture
def org(db):
    return Organization.objects.create(name="Agency")


@pytest.fixture
def ella(org):
    return Workspace.objects.create(organization=org, name="Ella", approval_workflow_mode="optional")


@pytest.fixture
def bruno(org):
    return Workspace.objects.create(organization=org, name="Bruno", approval_workflow_mode="optional")


@pytest.fixture
def secret(org):
    # A client nobody in the tests below is a member of, except its own owner.
    return Workspace.objects.create(organization=org, name="Secret client", approval_workflow_mode="optional")


def member(user, org, *workspaces, org_role="member", role="editor"):
    OrgMembership.objects.get_or_create(user=user, organization=org, defaults={"org_role": org_role})
    for workspace in workspaces:
        WorkspaceMembership.objects.create(user=user, workspace=workspace, workspace_role=role)
    return user


@pytest.fixture
def editor(org, ella, bruno):
    """An editor in two clients (Ella and Bruno); Ella is the current one."""
    user = member(make_user("editor@example.invalid"), org, ella, bruno)
    user.last_workspace_id = ella.id
    user.save(update_fields=["last_workspace_id"])
    return user


@pytest.fixture
def posts(org, ella, bruno, secret):
    owner = member(make_user("owner-content@example.invalid"), org, ella, bruno, secret, role="owner")
    return {
        "ella": create_content(ella, "Ella studio tour", owner),
        "bruno": create_content(bruno, "Bruno bakery reel", owner),
        "secret": create_content(secret, "Secret launch teaser", owner),
    }


# ---- AK1: the board shows the current workspace only ------------------------------------------


def test_board_shows_only_the_current_workspace_and_has_no_client_filter(editor, posts, ella):
    response = login(editor).get(BOARD)
    html = response.content.decode()
    assert response.status_code == 200
    assert "Ella studio tour" in html and "Bruno bakery reel" not in html and "Secret launch teaser" not in html
    assert 'id="filter-client"' not in html and "All clients" not in html and 'id="idea-client"' not in html
    assert response.context["workspace"] == ella and response.context["total"] == 1
    # The current client is named on the board; the switcher in the menu lists both clients.
    assert 'class="board-workspace"' in html and f"{BOARD}?client={posts['bruno'].workspace_id}" in html


def test_client_link_switches_the_current_workspace_for_members(editor, posts, bruno):
    client = login(editor)
    html = client.get(BOARD, {"client": bruno.id}).content.decode()
    assert "Bruno bakery reel" in html and "Ella studio tour" not in html
    editor.refresh_from_db()
    assert editor.last_workspace_id == bruno.id
    # The plain board now opens on Bruno, and so do the menu's New idea window and New content.
    html = client.get(BOARD).content.decode()
    assert "Bruno bakery reel" in html and "Ella studio tour" not in html
    assert f'<input type="hidden" name="workspace" value="{bruno.id}">' in html


def test_client_link_to_a_workspace_without_membership_is_refused(editor, posts, secret, ella):
    client = login(editor)
    assert client.get(BOARD, {"client": secret.id}).status_code == 404
    assert client.get(BOARD, {"client": "not-a-uuid"}).status_code == 404
    editor.refresh_from_db()
    assert editor.last_workspace_id == ella.id


def test_channel_of_another_client_is_refused(editor, ella, bruno):
    channel = create_manual_channel(bruno, "instagram", "Bruno IG")
    assert login(editor).get(BOARD, {"channel": channel.pk}).status_code == 400


def test_opening_content_of_another_client_switches_and_board_follows(editor, posts, bruno):
    """Links from notifications open the content detail of its workspace, which becomes current."""
    client = login(editor)
    client.get(BOARD)  # the board of Ella was shown last
    notification = Notification.objects.create(
        user=editor, event_type="comment_mention", title="New comment", data={"action_url": detail_url(posts["bruno"])}
    )
    assert notification.safe_action_url == detail_url(posts["bruno"])
    response = client.get(notification.safe_action_url)
    assert response.status_code == 200
    # The detail's "Content board" link returns to the board of the content's client.
    assert f"{BOARD}?client={bruno.id}" in response.content.decode()
    editor.refresh_from_db()
    assert editor.last_workspace_id == bruno.id
    assert "Bruno bakery reel" in client.get(BOARD).content.decode()


def test_content_of_a_client_without_membership_is_refused_and_nothing_switches(editor, posts, ella):
    client = login(editor)
    assert client.get(detail_url(posts["secret"])).status_code == 404
    editor.refresh_from_db()
    assert editor.last_workspace_id == ella.id


def test_without_a_last_workspace_the_earliest_joined_client_is_current_and_stored(editor, posts, ella):
    editor.last_workspace_id = None
    editor.save(update_fields=["last_workspace_id"])
    # Ella was joined first ("Bruno" sorts first in the menu, but the shared rule is the earliest membership).
    response = login(editor).get(BOARD)
    assert response.context["workspace"] == ella
    assert "Ella studio tour" in response.content.decode()
    editor.refresh_from_db()
    assert editor.last_workspace_id == ella.id


def test_web_and_oauth_mcp_use_the_same_default_workspace(editor, posts, ella, bruno):
    """R2: the board and the OAuth/MCP caller never work in different clients, also before anything was opened."""
    from apps.api.auth import _resolve_active_membership
    from apps.members.services import default_workspace_membership

    editor.last_workspace_id = None
    editor.save(update_fields=["last_workspace_id"])
    # One shared function: the same answer for the rule itself and for the OAuth resolver.
    assert default_workspace_membership(editor).workspace_id == ella.id
    assert _resolve_active_membership(editor).workspace_id == ella.id
    # Opening the board stores the default, so a later MCP call (which only reads last_workspace_id) agrees.
    page = login(editor).get(BOARD)
    assert page.context["workspace"] == ella
    editor.refresh_from_db()
    assert editor.last_workspace_id == ella.id
    assert _resolve_active_membership(editor).workspace_id == page.context["workspace"].id
    # A later switch in the menu moves both.
    login(editor).get(BOARD, {"client": bruno.id})
    editor.refresh_from_db()
    assert _resolve_active_membership(editor).workspace_id == bruno.id


def test_unusable_last_workspace_falls_back_to_the_earliest_membership(editor, posts, ella, bruno):
    from apps.api.auth import _resolve_active_membership

    bruno.is_archived = True
    bruno.save(update_fields=["is_archived"])
    editor.last_workspace_id = bruno.id
    editor.save(update_fields=["last_workspace_id"])
    response = login(editor).get(BOARD)
    assert response.context["workspace"] == ella
    editor.refresh_from_db()
    assert editor.last_workspace_id == ella.id
    assert _resolve_active_membership(editor).workspace_id == ella.id


def test_mcp_oauth_request_works_in_the_workspace_the_web_showed(editor, posts, ella):
    """R2 end to end: a bearer OAuth token authenticates into the workspace the board just showed."""
    from datetime import timedelta as delta

    from oauth2_provider.models import Application, get_access_token_model

    from apps.api.auth import _resolve_oauth_actor

    editor.last_workspace_id = None
    editor.save(update_fields=["last_workspace_id"])
    login(editor).get(BOARD)
    app = Application.objects.create(
        name="mcp",
        client_type="public",
        authorization_grant_type="authorization-code",
        redirect_uris="http://localhost/cb",
    )
    token = get_access_token_model().objects.create(
        user=editor, application=app, token="tok-b13-r2", scope="mcp", expires=timezone.now() + delta(hours=1)
    )
    actor = _resolve_oauth_actor(token.token)
    assert actor is not None and actor.workspace_id == ella.id


def _two_organizations(editor_user):
    """A second organization where the user has the earliest-joined workspace; the first one has none."""
    other = Organization.objects.create(name="Other agency")
    zed = Workspace.objects.create(organization=other, name="Zed", approval_workflow_mode="optional")
    OrgMembership.objects.create(user=editor_user, organization=other, org_role="member")
    return other, zed


def test_several_organizations_web_and_oauth_work_in_the_same_workspace(org):
    """R4: no usable last workspace in the selected organization; the user has a workspace only in another one."""
    from apps.api.auth import _resolve_active_membership

    user = make_user("two-orgs@example.invalid")
    OrgMembership.objects.create(user=user, organization=org, org_role="member")  # member, no workspace here
    other, zed = _two_organizations(user)
    WorkspaceMembership.objects.create(user=user, workspace=zed, workspace_role="editor")
    user.last_workspace_id = None
    user.save(update_fields=["last_workspace_id"])
    client = login(user)
    session = client.session
    session["active_organization_id"] = str(org.id)  # the selection points at the organization without a workspace
    session.save()
    assert _resolve_active_membership(user).workspace_id == zed.id
    # The user is not an organization admin, so the page opens in Zed (and the selection follows it).
    response = client.get(BOARD)
    assert response.status_code == 200 and response.context["workspace"] == zed
    user.refresh_from_db()
    assert user.last_workspace_id == zed.id
    assert client.session["active_organization_id"] == str(other.id)
    assert _resolve_active_membership(user).workspace_id == response.context["workspace"].id


def test_earliest_membership_wins_across_organizations_when_no_organization_is_selected(org, ella):
    from apps.api.auth import _resolve_active_membership

    user = make_user("across@example.invalid")
    other, zed = _two_organizations(user)
    WorkspaceMembership.objects.create(user=user, workspace=zed, workspace_role="editor")
    member(user, org, ella)  # joined later
    user.last_workspace_id = None
    user.save(update_fields=["last_workspace_id"])
    client = login(user)
    response = client.get(BOARD)
    assert response.context["workspace"] == zed
    assert _resolve_active_membership(user).workspace_id == zed.id


def test_organization_admin_without_workspace_in_selected_organization_keeps_it(org, ella):
    """An organization admin who selected an organization where they have no workspace stays in it."""
    user = make_user("admin-only@example.invalid")
    OrgMembership.objects.create(user=user, organization=org, org_role="admin")
    other, zed = _two_organizations(user)
    WorkspaceMembership.objects.create(user=user, workspace=zed, workspace_role="editor")
    user.last_workspace_id = None
    user.save(update_fields=["last_workspace_id"])
    client = login(user)
    session = client.session
    session["active_organization_id"] = str(org.id)
    session.save()
    response = client.get(reverse("organizations:settings"))
    assert response.status_code == 200
    assert client.session["active_organization_id"] == str(org.id)


def test_entering_the_app_uses_the_shared_rule_and_stores_it(editor, ella, bruno):
    """R5: the entry page does not pick the first workspace alphabetically (Bruno) but the earliest membership (Ella)."""
    editor.last_workspace_id = None
    editor.save(update_fields=["last_workspace_id"])
    response = login(editor).get(reverse("dashboard"))
    assert response.status_code == 302 and str(ella.id) in response["Location"]
    editor.refresh_from_db()
    assert editor.last_workspace_id == ella.id


def test_entering_the_app_with_an_unusable_last_workspace(editor, ella, bruno):
    bruno.is_archived = True
    bruno.save(update_fields=["is_archived"])
    editor.last_workspace_id = bruno.id
    editor.save(update_fields=["last_workspace_id"])
    response = login(editor).get(reverse("dashboard"))
    assert response.status_code == 302 and str(ella.id) in response["Location"]
    editor.refresh_from_db()
    assert editor.last_workspace_id == ella.id


def test_switching_organization_picks_the_shared_default_not_the_alphabetical_one(org, ella, bruno):
    user = make_user("switch@example.invalid")
    other = Organization.objects.create(name="Other agency")
    OrgMembership.objects.create(user=user, organization=other, org_role="member")
    OrgMembership.objects.create(user=user, organization=org, org_role="member")
    # Ella joined first although Bruno sorts first.
    WorkspaceMembership.objects.create(user=user, workspace=ella, workspace_role="editor")
    WorkspaceMembership.objects.create(user=user, workspace=bruno, workspace_role="editor")
    user.last_workspace_id = None
    user.save(update_fields=["last_workspace_id"])
    login(user).post(reverse("organizations:switch"), {"organization_id": str(org.id)})
    user.refresh_from_db()
    assert user.last_workspace_id == ella.id


def test_a_user_without_clients_sees_an_empty_board(org):
    lonely = member(make_user("lonely@example.invalid"), org)
    response = login(lonely).get(BOARD)
    assert response.status_code == 200 and response.context["total"] == 0
    assert "You do not have access to any clients yet." in response.content.decode()


# ---- P2-B09 × P2-B13: search, trash and bulk actions of the board stay in the current workspace -


TRASH = reverse("jrc_production:trash")


def test_board_keeps_search_trash_and_bulk_actions_without_a_client_filter(editor, posts):
    html = login(editor).get(BOARD).content.decode()
    assert 'id="full-search"' in html and 'id="quick-search"' in html and f'href="{TRASH}"' in html
    assert "data-bulk-move" in html and 'id="card-action-menu"' in html
    assert 'id="filter-client"' not in html and "All clients" not in html


def test_search_finds_content_of_the_current_workspace_only(editor, posts, bruno):
    # The words are in both clients' titles the editor may read; only the current client's card shows.
    Post.objects.filter(pk=posts["bruno"].pk).update(title="Bruno studio tour")
    client = login(editor)
    response = client.get(BOARD, {"q": "studio tour"})
    html = response.content.decode()
    assert response.status_code == 200 and response.context["total"] == 1
    assert "Ella studio tour" in html and "Bruno studio tour" not in html
    # A client link with a search shows the other client's match, and that client becomes current.
    response = client.get(BOARD, {"client": bruno.id, "q": "studio tour"})
    html = response.content.decode()
    assert response.context["total"] == 1 and "Bruno studio tour" in html and "Ella studio tour" not in html
    # Content of a client the editor is not a member of is never searched.
    assert client.get(BOARD, {"q": "Secret launch"}).context["total"] == 0


def test_trash_lists_the_current_workspace_only(editor, posts, ella, bruno):
    from apps.jrc_production.trash import trash_content

    owner = User.objects.get(email="owner-content@example.invalid")
    for post in posts.values():
        trash_content(post, owner)
    client = login(editor)
    html = client.get(TRASH).content.decode()
    assert "Ella studio tour" in html and "Bruno bakery reel" not in html and "Secret launch teaser" not in html
    # Switching the client (here via the board link, as the menu does) switches the trash too.
    assert client.get(BOARD, {"client": bruno.id}).status_code == 200
    html = client.get(TRASH).content.decode()
    assert "Bruno bakery reel" in html and "Ella studio tour" not in html and "Secret launch teaser" not in html


def test_trash_of_a_user_without_clients_is_empty(org, posts):
    from apps.jrc_production.trash import trash_content

    trash_content(posts["ella"], User.objects.get(email="owner-content@example.invalid"))
    lonely = member(make_user("lonely-trash@example.invalid"), org)
    response = login(lonely).get(TRASH)
    assert response.status_code == 200 and "Trash is empty." in response.content.decode()


# ---- AK2: new content goes to the current workspace only --------------------------------------


def test_new_idea_window_and_new_content_page_have_no_client_choice(editor, ella):
    client = login(editor)
    board = client.get(BOARD).content.decode()
    assert 'id="new-idea-client"' not in board and "Choose a client" not in board
    assert f'<input type="hidden" name="workspace" value="{ella.id}">' in board
    page = client.get(NEW).content.decode()
    assert 'id="new-content-client"' not in page and "Choose a client" not in page
    assert f'<input type="hidden" name="workspace" value="{ella.id}">' in page and "Ella · New content" in page


def test_new_content_link_naming_a_client_opens_it(editor, bruno):
    client = login(editor)
    page = client.get(NEW, {"client": bruno.id, "date": "2030-01-02"}).content.decode()
    assert "Bruno · New content" in page and f'value="{bruno.id}"' in page
    editor.refresh_from_db()
    assert editor.last_workspace_id == bruno.id


def test_create_in_the_current_workspace(editor, ella):
    response = login(editor).post(CREATE, {"workspace": ella.id, "title": "Morning walk", "origin": "idea"}, **XHR)
    assert response.status_code == 201
    assert Post.objects.get(title="Morning walk").workspace == ella


@pytest.mark.parametrize("origin", ["idea", "page"])
def test_create_for_another_member_workspace_is_refused(editor, bruno, origin):
    """A forged or stale form (the menu switched in another tab) never lands at another client."""
    client = login(editor)
    response = client.post(CREATE, {"workspace": bruno.id, "title": "Wrong place", "origin": origin}, **XHR)
    assert response.status_code == 409
    assert "Nothing was saved" in response.content.decode()
    response = client.post(CREATE, {"workspace": bruno.id, "title": "Wrong place", "origin": origin})
    # R3: an ordinary form post is refused with 409 as well, with the notice on the page.
    assert response.status_code == 409
    assert "Nothing was saved" in response.content.decode() and "Reload the page" in response.content.decode()
    assert not Post.objects.filter(title="Wrong place").exists()


def test_create_for_a_workspace_without_membership_is_missing(editor, secret):
    response = login(editor).post(CREATE, {"workspace": secret.id, "title": "Intruder"}, **XHR)
    assert response.status_code == 404
    assert not Post.objects.filter(title="Intruder").exists()


def test_quick_idea_only_in_the_current_workspace(editor, ella, bruno, secret):
    client = login(editor)
    assert client.post(IDEA, {"workspace": ella.id, "title": "Quick one"}, **XHR).status_code == 201
    assert client.post(IDEA, {"workspace": bruno.id, "title": "Quick two"}, **XHR).status_code == 409
    plain = client.post(IDEA, {"workspace": bruno.id, "title": "Quick plain"})
    assert plain.status_code == 409 and "Nothing was saved" in plain.content.decode()
    assert not Post.objects.filter(title__in=["Quick two", "Quick plain"]).exists()
    assert client.post(IDEA, {"workspace": secret.id, "title": "Quick three"}, **XHR).status_code == 404
    assert list(Post.objects.filter(title__startswith="Quick").values_list("title", "workspace")) == [
        ("Quick one", ella.id)
    ]


def test_after_switching_the_other_client_accepts_new_content(editor, bruno):
    client = login(editor)
    client.get(BOARD, {"client": bruno.id})
    assert client.post(CREATE, {"workspace": bruno.id, "title": "Now at Bruno"}, **XHR).status_code == 201
    assert Post.objects.get(title="Now at Bruno").workspace == bruno


def test_viewer_of_the_current_workspace_gets_no_new_buttons(org, ella, bruno):
    viewer = member(make_user("viewer@example.invalid"), org, ella, role="viewer")
    html = login(viewer).get(BOARD).content.decode()
    assert "+ New idea" not in html and 'id="quick-idea"' not in html and 'id="jrc-new-idea"' not in html


def test_api_creates_only_in_the_key_workspace(org, ella, bruno):
    """The API key is bound to one workspace; a submitted workspace or another client's channel changes nothing."""
    from apps.api_keys import services as key_services

    owner = member(make_user("api-owner@example.invalid"), org, ella, bruno, org_role="owner", role="owner")
    ella_channel = create_manual_channel(ella, "instagram", "Ella IG")
    bruno_channel = create_manual_channel(bruno, "instagram", "Bruno IG")
    key = key_services.issue_api_key(
        workspace=ella, social_accounts=[ella_channel], issued_by=owner, name="p2b13", permissions=list(PERMISSION_KEYS)
    )
    api = Client(HTTP_AUTHORIZATION=f"Bearer {key.plaintext_token}")

    def post(payload):
        return api.post("/api/v1/posts/", data=json.dumps(payload), content_type="application/json", secure=True)

    response = post(
        {"social_account_id": str(ella_channel.id), "caption": "Via API", "action": "draft", "workspace": str(bruno.id)}
    )
    assert response.status_code == 201, response.content
    assert Post.objects.get(caption="Via API").workspace == ella
    assert (
        post({"social_account_id": str(bruno_channel.id), "caption": "Elsewhere", "action": "draft"}).status_code == 403
    )
    assert not Post.objects.filter(caption="Elsewhere").exists()


# ---- AK3: admin overview ------------------------------------------------------------------------


@pytest.fixture
def org_owner(org):
    return member(make_user("org-owner@example.invalid"), org, org_role="owner")


@pytest.fixture
def org_admin(org, ella):
    # An organization admin who is a member of Ella only.
    return member(make_user("org-admin@example.invalid"), org, ella, org_role="admin", role="manager")


@pytest.mark.parametrize("who", ["org_owner", "org_admin"])
def test_owner_and_admin_see_every_client_in_the_overview(request, who, posts, ella, bruno, secret):
    person = request.getfixturevalue(who)
    client = login(person)
    response = client.get(OVERVIEW)
    html = response.content.decode()
    assert response.status_code == 200
    for title in ("Ella studio tour", "Bruno bakery reel", "Secret launch teaser"):
        assert title in html
    # Client colour and name on every card, links to the read-only summary, nothing to change.
    card = html[html.index(f'data-client="{secret.id}"') :]
    card = card[: card.index("</article>")]
    assert secret.accent_color in card and "Secret client" in card and overview_url(posts["secret"]) in card
    page = html.split('class="ov-page"', 1)[1].split("</main>", 1)[0]
    assert 'method="post"' not in page and "Move…" not in page and "+ New idea" not in page
    assert f'href="{OVERVIEW}"' in html and ">Admin<" in html


def test_overview_client_filter(org_owner, posts, bruno):
    client = login(org_owner)
    html = client.get(OVERVIEW, {"client": bruno.id}).content.decode()
    assert "Bruno bakery reel" in html and "Ella studio tour" not in html and "Secret launch teaser" not in html
    other = Workspace.objects.create(organization=Organization.objects.create(name="Other org"), name="Foreign")
    assert client.get(OVERVIEW, {"client": other.id}).status_code == 400
    assert client.get(OVERVIEW, {"client": "nonsense"}).status_code == 400


def test_overview_hides_other_organizations_and_archived_clients(org_owner, posts, org):
    other_org = Organization.objects.create(name="Other org")
    foreign_ws = Workspace.objects.create(organization=other_org, name="Foreign client")
    foreign = Post.objects.create(workspace=foreign_ws, title="Foreign content")
    archived = Workspace.objects.create(organization=org, name="Old client", is_archived=True)
    old = Post.objects.create(workspace=archived, title="Archived content")
    client = login(org_owner)
    html = client.get(OVERVIEW).content.decode()
    assert "Foreign content" not in html and "Archived content" not in html
    assert client.get(overview_url(foreign)).status_code == 404
    assert client.get(overview_url(old)).status_code == 404


@pytest.mark.parametrize("role", ["owner", "manager", "editor", "contributor", "client", "viewer"])
def test_nobody_else_sees_the_overview(org, ella, posts, role):
    """Workspace roles (even a workspace owner) with an ordinary organization membership get 404 and no menu item."""
    person = member(make_user(f"{role}@example.invalid"), org, ella, role=role)
    client = login(person)
    for url in (OVERVIEW, CALENDAR, overview_url(posts["ella"]), overview_url(posts["secret"])):
        assert client.get(url).status_code == 404, url
    assert f'href="{OVERVIEW}"' not in client.get(BOARD).content.decode()


def test_overview_is_read_only(org_owner, posts):
    client = login(org_owner)
    for url in (OVERVIEW, overview_url(posts["secret"])):
        assert client.post(url, {"title": "x"}).status_code in (403, 405)


def _notes(post):
    PostComment.objects.create(post=post, author=post.author, body="TEAM ONLY NOTE", visibility="internal")
    PostComment.objects.create(post=post, author=post.author, body="CLIENT VISIBLE NOTE", visibility="external")


def test_admin_reads_content_of_a_client_without_membership(org_admin, posts, secret):
    post = posts["secret"]
    _notes(post)
    client = login(org_admin)
    response = client.get(overview_url(post))
    html = response.content.decode()
    assert response.status_code == 200
    assert "Secret launch teaser" in html and "Secret client" in html
    # R1: without membership an organization admin sees what a client sees, not the Team only notes.
    assert "CLIENT VISIBLE NOTE" in html and "TEAM ONLY NOTE" not in html
    assert "You are not a member of this client" in html and detail_url(post) not in html
    assert "<form" not in html.split('class="ovc"', 1)[1].split("</main>", 1)[0]
    # The regular detail and every write still need the membership.
    assert client.get(detail_url(post)).status_code == 404
    rename = reverse("jrc_production:rename", kwargs={"workspace_id": secret.id, "post_id": post.pk})
    assert client.post(rename, {"title": "Hijacked"}).status_code == 404
    post.refresh_from_db()
    assert post.title == "Secret launch teaser"
    # Reading the overview never makes a client current.
    org_admin.refresh_from_db()
    assert org_admin.last_workspace_id != secret.id


def test_org_owner_without_membership_sees_team_only_notes(org_owner, posts):
    _notes(posts["secret"])
    html = login(org_owner).get(overview_url(posts["secret"])).content.decode()
    assert "TEAM ONLY NOTE" in html and "CLIENT VISIBLE NOTE" in html


@pytest.mark.parametrize(
    ("org_role", "ws_role", "sees_internal"),
    [
        ("owner", "client", False),
        ("owner", "viewer", False),
        ("admin", "client", False),
        ("admin", "viewer", False),
        ("admin", "manager", True),
        ("admin", "editor", True),
        ("owner", "manager", True),
    ],
)
def test_overview_follows_the_workspace_role_for_members(org, posts, ella, org_role, ws_role, sees_internal):
    """R1: a member sees what the workspace role sees, whatever the organization role is."""
    _notes(posts["ella"])
    person = member(make_user(f"{org_role}-{ws_role}@example.invalid"), org, ella, org_role=org_role, role=ws_role)
    overview = login(person).get(overview_url(posts["ella"])).content.decode()
    assert "CLIENT VISIBLE NOTE" in overview
    assert ("TEAM ONLY NOTE" in overview) is sees_internal
    # The regular detail's comment selector gives the same answer.
    from apps.approvals.comments import get_comments_for_post

    shown = [c.body for c in get_comments_for_post(posts["ella"], person)]
    assert ("TEAM ONLY NOTE" in shown) is sees_internal


def test_admin_member_gets_a_link_to_the_regular_detail(org_admin, posts):
    html = login(org_admin).get(overview_url(posts["ella"])).content.decode()
    assert detail_url(posts["ella"]) in html and "Open in Ella" in html


def test_overview_calendar_shows_all_clients_read_only(org_owner, org, posts, secret, ella):
    planner = member(make_user("planner@example.invalid"), org, secret, ella, role="owner")
    channel = create_manual_channel(secret, "instagram", "Secret IG")
    post = create_content(secret, "Planned secret", planner, social_account_ids=[channel.pk])
    when = timezone.now().replace(hour=10, minute=0, second=0, microsecond=0) + timedelta(days=1)
    set_publication_date(post, PlatformPost.objects.get(post=post).pk, planner, when)
    client = login(org_owner)
    html = client.get(CALENDAR, {"date": when.date().isoformat()}).content.decode()
    assert "Planned secret" in html and overview_url(post) in html and secret.accent_color in html
    assert "Secret client" in html and "Admin overview" in html
    calendar = html.split('class="cross-calendar-main"', 1)[1].split("</main>", 1)[0]
    assert f'href="{NEW}' not in calendar and 'class="cal-add-btn"' not in calendar
    # Client filter of the calendar ignores ids of other organizations.
    other = Workspace.objects.create(organization=Organization.objects.create(name="X"), name="X client")
    assert client.get(CALENDAR, {"workspace": other.id}).status_code == 200
    filtered = client.get(CALENDAR, {"workspace": ella.id, "date": when.date().isoformat()}).content.decode()
    assert "Planned secret" not in filtered


def test_org_member_without_admin_role_has_no_all_calendars_in_settings(editor):
    html = login(editor).get(reverse("accounts:settings")).content.decode()
    assert f'href="{CALENDAR}"' not in html


def test_remembering_the_default_never_writes_a_deleted_account(org, ella):
    """Review 3, P2: a view that deletes the account must not end in a 500 from the middleware."""
    from django.contrib.auth import get_user_model
    from django.http import HttpResponseRedirect
    from django.test import RequestFactory

    from apps.members.middleware import RBACMiddleware

    user = make_user("gone@example.invalid")
    member(user, org, ella)
    user.last_workspace_id = None
    user.save(update_fields=["last_workspace_id"])

    def view(request):
        get_user_model().objects.filter(pk=request.user.pk).delete()
        return HttpResponseRedirect("/")

    request = RequestFactory().get("/jrc/board/")
    request.user = user
    from django.contrib.sessions.backends.db import SessionStore

    request.session = SessionStore()
    response = RBACMiddleware(view)(request)
    assert response.status_code == 302
    assert not get_user_model().objects.filter(pk=user.pk).exists()


# ---- P2-INT3: the overview with outputs (P2-B14) and comments on versions (P2-B16) ----------------


@pytest.fixture
def two_outputs(posts, ella):
    """Ella's content with two outputs of one YouTube channel: Video (long form) and Shorts (short form)."""
    from apps.jrc_production.services import add_content_channel

    post = posts["ella"]
    channel = create_manual_channel(ella, "youtube", "Ella YT")
    add_content_channel(post, channel.pk, post.author, "long_form")
    add_content_channel(post, channel.pk, post.author, "short_form")
    return post


def test_overview_board_shows_a_channel_once_with_the_formats_of_its_outputs(org_owner, two_outputs, ella):
    html = login(org_owner).get(OVERVIEW).content.decode()
    card = html[html.index(f'data-client="{ella.id}"') :]
    card = card[: card.index("</article>")]
    assert card.count('class="ov-channel"') == 1
    assert "Video, Shorts" in card and "Ella YT" in card


def test_overview_summary_keeps_outputs_apart(org_owner, two_outputs):
    import uuid

    from apps.jrc_production import shotlist
    from apps.jrc_texts.models import TextDocument, TextVersion
    from apps.jrc_video.models import VideoAsset, VideoVersion

    post = two_outputs
    long_form = post.platform_posts.get(output_format="long_form")
    short_form = post.platform_posts.get(output_format="short_form")
    for output, text in ((long_form, "Long caption text"), (short_form, "Shorts caption text")):
        document = TextDocument.objects.create(
            post=post, kind="caption", platform_post=output, workspace=post.workspace, format="plain"
        )
        TextVersion.objects.create(
            document=document,
            number=1,
            state="final",
            content={"text": text},
            text=text,
            created_by=post.author,
            finalized_at=timezone.now(),
        )
        asset = VideoAsset.objects.create(platform_post=output, ff_asset_id=uuid.uuid4())
        VideoVersion.objects.create(video=asset, number=1, ff_version_id=uuid.uuid4(), status="ready")
    shotlist.add_item(post, post.author, "Vertical hook shot", output_format="short_form")
    shotlist.add_item(post, post.author, "Wide intro shot", output_format="long_form")

    html = login(org_owner).get(overview_url(post)).content.decode()
    assert "YouTube · Video · Ella YT" in html and "YouTube · Shorts · Ella YT" in html
    captions = html.split('id="ovc-caption"', 1)[1].split("</section>", 1)[0]
    assert captions.index("YouTube · Video") < captions.index("Long caption text")
    assert captions.index("YouTube · Shorts") < captions.index("Shorts caption text")
    videos = html.split('id="ovc-video"', 1)[1].split("</section>", 1)[0]
    assert "YouTube · Video · Ella YT · v1" in videos and "YouTube · Shorts · Ella YT · v1" in videos
    # One shotlist per format; the automatic thumbnail item is in the first list.
    lists = html.split('id="ovc-shotlist"', 1)[1].split("</section>", 1)[0]
    long_list, short_list = lists.split("<h3>Short form</h3>")
    assert "<h3>Long form</h3>" in long_list and "Wide intro shot" in long_list and "Thumbnail photo" in long_list
    assert "Vertical hook shot" in short_list and "Wide intro shot" not in short_list


def test_overview_summary_labels_comments_on_versions_and_keeps_visibility(org_admin, org_owner, posts):
    from apps.approvals import comments
    from apps.jrc_production import shotlist

    post = posts["secret"]
    version = shotlist.snapshot(post)
    comments.create_comment(post, post.author, "TEAM SHOTLIST NOTE", shotlist_version_id=version.pk)
    shared = comments.create_comment(
        post, post.author, "CLIENT SHOTLIST NOTE", visibility="external", shotlist_version_id=version.pk
    )
    comments.create_comment(post, post.author, "SHOTLIST REPLY", parent_id=shared.pk, shotlist_version_id=version.pk)
    owner_html = login(org_owner).get(overview_url(post)).content.decode()
    discussion = owner_html.split('id="ovc-discussion"', 1)[1].split("</section>", 1)[0]
    assert "TEAM SHOTLIST NOTE" in discussion and "CLIENT SHOTLIST NOTE" in discussion
    assert discussion.count("Shotlist · V1") == 3 and "SHOTLIST REPLY" in discussion
    # An organization admin without membership still reads only what a client reads.
    admin_html = login(org_admin).get(overview_url(post)).content.decode()
    assert "CLIENT SHOTLIST NOTE" in admin_html and "TEAM SHOTLIST NOTE" not in admin_html


def test_overview_calendar_names_the_format_of_each_output(org_owner, two_outputs):
    post = two_outputs
    when = timezone.now().replace(hour=10, minute=0, second=0, microsecond=0) + timedelta(days=1)
    for output in post.platform_posts.all():
        set_publication_date(post, output.pk, post.author, when)
    html = login(org_owner).get(CALENDAR, {"date": when.date().isoformat()}).content.decode()
    calendar = html.split('class="cross-calendar-main"', 1)[1].split("</main>", 1)[0]
    assert ">Video</span>" in calendar and ">Shorts</span>" in calendar
    assert "YouTube · Shorts · Ella YT" in calendar
