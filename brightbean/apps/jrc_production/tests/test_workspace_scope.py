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


def test_without_a_last_workspace_the_first_listed_client_is_current(editor, posts, bruno):
    editor.last_workspace_id = None
    editor.save(update_fields=["last_workspace_id"])
    # "Bruno" sorts before "Ella", as in the menu.
    response = login(editor).get(BOARD)
    assert response.context["workspace"] == bruno
    assert "Bruno bakery reel" in response.content.decode()


def test_a_user_without_clients_sees_an_empty_board(org):
    lonely = member(make_user("lonely@example.invalid"), org)
    response = login(lonely).get(BOARD)
    assert response.status_code == 200 and response.context["total"] == 0
    assert "You do not have access to any clients yet." in response.content.decode()


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
    assert response.status_code == 302
    assert not Post.objects.filter(title="Wrong place").exists()


def test_create_for_a_workspace_without_membership_is_missing(editor, secret):
    response = login(editor).post(CREATE, {"workspace": secret.id, "title": "Intruder"}, **XHR)
    assert response.status_code == 404
    assert not Post.objects.filter(title="Intruder").exists()


def test_quick_idea_only_in_the_current_workspace(editor, ella, bruno, secret):
    client = login(editor)
    assert client.post(IDEA, {"workspace": ella.id, "title": "Quick one"}, **XHR).status_code == 201
    assert client.post(IDEA, {"workspace": bruno.id, "title": "Quick two"}, **XHR).status_code == 409
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


def test_admin_reads_content_of_a_client_without_membership(org_admin, posts, secret):
    post = posts["secret"]
    PostComment.objects.create(post=post, author=post.author, body="TEAM ONLY NOTE", visibility="internal")
    PostComment.objects.create(post=post, author=post.author, body="CLIENT VISIBLE NOTE", visibility="external")
    client = login(org_admin)
    response = client.get(overview_url(post))
    html = response.content.decode()
    assert response.status_code == 200
    assert "Secret launch teaser" in html and "Secret client" in html
    assert "TEAM ONLY NOTE" in html and "CLIENT VISIBLE NOTE" in html and "Team only" in html
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
    calendar = html.split('class="cross-calendar-main"', 1)[1]
    assert f'href="{NEW}' not in calendar and 'class="cal-add-btn"' not in calendar
    # Client filter of the calendar ignores ids of other organizations.
    other = Workspace.objects.create(organization=Organization.objects.create(name="X"), name="X client")
    assert client.get(CALENDAR, {"workspace": other.id}).status_code == 200
    filtered = client.get(CALENDAR, {"workspace": ella.id, "date": when.date().isoformat()}).content.decode()
    assert "Planned secret" not in filtered


def test_org_member_without_admin_role_has_no_all_calendars_in_settings(editor):
    html = login(editor).get(reverse("accounts:settings")).content.decode()
    assert f'href="{CALENDAR}"' not in html
