"""P2-B03: no way into the old composer, the New idea window, the New content page and the board order."""

import re
from datetime import timedelta
from pathlib import Path

import pytest
from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.composer.models import Post
from apps.jrc_channels.services import create_manual_channel
from apps.jrc_production.models import PipelineStage
from apps.jrc_production.services import create_content, initial_stages, pipeline_stage
from apps.jrc_production.views import _sort_cards
from apps.jrc_texts.models import TextDocument, TextVersion
from apps.members.models import WorkspaceMembership
from apps.workspaces.models import Workspace

pytestmark = pytest.mark.django_db

XHR = {"HTTP_X_REQUESTED_WITH": "XMLHttpRequest"}
CREATE = "jrc_production:create"
NEW = "jrc_production:new_content"
BOARD = "jrc_production:board"


@pytest.fixture
def http(client, user):
    client.force_login(user)
    return client


@pytest.fixture
def channel(workspace):
    return create_manual_channel(workspace, "instagram", "Ella IG")


def member(workspace, role, email=None):
    person = User.objects.create_user(
        email=email or f"{role}-{workspace.pk.hex[:6]}@example.invalid",
        password="x",
        tos_accepted_at=timezone.now(),
    )
    WorkspaceMembership.objects.create(workspace=workspace, user=person, workspace_role=role)
    WorkspaceMembership.objects.filter(user=person).exclude(workspace=workspace).delete()
    return person


def detail(post, tab=""):
    url = reverse("jrc_production:content_detail", kwargs={"workspace_id": post.workspace_id, "post_id": post.pk})
    return f"{url}?tab={tab}" if tab else url


def concept(post):
    return TextVersion.objects.get(document__post=post, document__kind=TextDocument.Kind.CONCEPT)


# --- AK5: board order --------------------------------------------------------------------------------


def test_column_dated_first_nearest_on_top_then_undated_newest_at_the_bottom(http, workspace, user):
    now = timezone.now()
    oldest = create_content(workspace, "Oldest undated", user)
    late = create_content(workspace, "Dated later", user)
    newest = create_content(workspace, "Newest undated", user)
    soon = create_content(workspace, "Dated soon", user)
    # A recent change must not lift older content above newer: creation decides among undated cards.
    Post.objects.filter(pk=oldest.pk).update(updated_at=now + timedelta(hours=1))
    Post.objects.filter(pk=late.pk).update(proposed_publish_at=now + timedelta(days=9))
    Post.objects.filter(pk=soon.pk).update(proposed_publish_at=now + timedelta(days=2))
    response = http.get(reverse(BOARD), {"client": workspace.pk})
    column = next(c for c in response.context["columns"] if c["value"] == "idea")
    assert [card["post"].pk for card in column["cards"]] == [soon.pk, late.pk, oldest.pk, newest.pk]


def test_planned_date_counts_like_a_proposed_one(workspace, user):
    now = timezone.now()
    cards = [
        {"post": Post(title="undated", created_at=now - timedelta(days=3)), "date": None},
        {"post": Post(title="planned", created_at=now), "date": now + timedelta(days=1)},
        {"post": Post(title="overdue", created_at=now), "date": now - timedelta(days=1)},
    ]
    assert [c["post"].title for c in _sort_cards(list(cards))] == ["overdue", "planned", "undated"]


def test_published_column_keeps_newest_first():
    now = timezone.now()
    cards = [
        {"post": Post(title="old", updated_at=now, created_at=now), "date": now - timedelta(days=3)},
        {"post": Post(title="new", updated_at=now, created_at=now - timedelta(days=9)), "date": now},
        {"post": Post(title="undated", updated_at=now, created_at=now), "date": None},
    ]
    assert [c["post"].title for c in _sort_cards(list(cards), newest_first=True)] == ["new", "old", "undated"]


# --- AK3/AK4: one creation service --------------------------------------------------------------------


def test_description_becomes_concept_version_one_as_plain_text(workspace, user):
    post = create_content(workspace, "Idea", user, description="  First *line*\r\nsecond <b>x</b>\n\n# Not a heading  ")
    version = concept(post)
    assert version.number == 1 and version.state == TextVersion.State.FINAL and version.created_by == user
    doc = version.content
    assert [node["type"] for node in doc["content"]] == ["paragraph", "paragraph"]
    first = doc["content"][0]["content"]
    assert first[0] == {"type": "text", "text": "First *line*"} and first[1]["type"] == "hardBreak"
    assert first[2]["text"] == "second <b>x</b>"
    assert doc["content"][1]["content"][0]["text"] == "# Not a heading"
    assert pipeline_stage(post) == "idea"


def test_no_description_no_concept_and_too_long_description_saves_nothing(workspace, user):
    post = create_content(workspace, "Idea", user, description="   ")
    assert not TextDocument.objects.filter(post=post).exists()
    with pytest.raises(ValidationError):
        create_content(workspace, "Too long", user, description="x" * 5001)
    assert not Post.objects.filter(title="Too long").exists()


def test_client_creates_an_idea_with_description_but_may_not_edit_texts(organization, workspace):
    client_user = member(workspace, "client")
    post = create_content(workspace, "Client idea", client_user, description="Shot on the beach")
    assert concept(post).created_by == client_user
    from apps.jrc_texts.services import can_edit_text, create_initial_concept

    membership = WorkspaceMembership.objects.get(user=client_user, workspace=workspace)
    assert not can_edit_text(post, membership)
    # The initial concept exists once and only for the author's own new content.
    with pytest.raises(PermissionDenied):
        create_initial_concept(post, client_user, "again")
    other = create_content(workspace, "Team idea", member(workspace, "editor"))
    with pytest.raises(PermissionDenied):
        create_initial_concept(other, client_user, "not mine")


def test_viewer_cannot_create(workspace):
    with pytest.raises(PermissionDenied):
        create_content(workspace, "Nope", member(workspace, "viewer"), description="x")


@pytest.mark.parametrize("stage", ["script", "shooting", "editing"])
def test_production_stage_is_reached_by_a_move_from_idea(workspace, user, stage):
    post = create_content(workspace, "Staged", user, stage=stage)
    assert pipeline_stage(post) == stage
    change = post.jrc_stage_changes.get()
    assert (change.from_stage, change.to_stage, change.user) == ("idea", stage, user)


def test_review_needs_a_channel_and_nothing_is_saved_without(workspace, user, channel):
    with pytest.raises(ValidationError, match="Add a channel"):
        create_content(workspace, "Review", user, stage="review", description="text")
    assert not Post.objects.filter(title="Review").exists()
    assert not TextDocument.objects.exists()
    post = create_content(workspace, "Review", user, stage="review", social_account_ids=[channel.pk])
    assert pipeline_stage(post) == "review"


@pytest.mark.parametrize("stage", ["approved", "published", "bogus"])
def test_approved_and_published_are_never_a_starting_stage(workspace, user, channel, stage):
    with pytest.raises(ValidationError, match="stage"):
        create_content(workspace, "Skip", user, stage=stage, social_account_ids=[channel.pk])
    assert not Post.objects.filter(title="Skip").exists()


def test_ready_needs_a_planner_a_mode_without_required_approval_and_a_future_date(workspace, user, channel):
    future = timezone.now() + timedelta(days=3)
    with pytest.raises(ValidationError, match="future"):
        create_content(workspace, "Ready", user, stage="ready", social_account_ids=[channel.pk])
    post = create_content(workspace, "Ready", user, stage="ready", social_account_ids=[channel.pk], publish_at=future)
    assert pipeline_stage(post) == "ready" and post.scheduled_at == future and post.proposed_publish_at is None
    workspace.approval_workflow_mode = "required_internal"
    workspace.save(update_fields=["approval_workflow_mode"])
    with pytest.raises(ValidationError, match="stage"):
        create_content(workspace, "Ready 2", user, stage="ready", social_account_ids=[channel.pk], publish_at=future)


def test_publish_date_of_other_stages_is_a_proposal(workspace, user):
    when = timezone.now() + timedelta(days=5)
    post = create_content(workspace, "Proposed", user, stage="script", publish_at=when)
    assert post.proposed_publish_at == when and post.scheduled_at is None


@pytest.mark.parametrize("mode", ["none", "optional", "required_internal", "required_internal_and_client"])
@pytest.mark.parametrize("role", ["owner", "manager", "editor", "contributor", "client", "viewer"])
def test_offered_stages_are_exactly_the_ones_creation_accepts(organization, mode, role):
    """``initial_stages`` (what the page offers) and ``create_content`` (the move rules) never disagree."""
    workspace = Workspace.objects.create(organization=organization, name=f"{mode}-{role}", approval_workflow_mode=mode)
    person = member(workspace, role)
    account = create_manual_channel(workspace, "instagram", "IG")
    membership = WorkspaceMembership.objects.get(user=person, workspace=workspace)
    offered = set(initial_stages(membership))
    future = timezone.now() + timedelta(days=2)
    for stage in PipelineStage.values:
        try:
            create_content(
                workspace, f"{stage}", person, stage=stage, social_account_ids=[account.pk], publish_at=future
            )
        except (ValidationError, PermissionDenied):
            accepted = False
        else:
            accepted = True
        assert accepted == (stage in offered), (mode, role, stage)
    assert PipelineStage.APPROVED not in offered and PipelineStage.PUBLISHED not in offered


# --- the create view: window and page -----------------------------------------------------------------


def test_window_creates_and_returns_to_the_page_with_a_linked_notice(http, workspace, user, channel):
    page = detail(create_content(workspace, "Elsewhere", user), "shotlist")
    response = http.post(
        reverse(CREATE),
        {
            "workspace": workspace.pk,
            "origin": "idea",
            "stage": "idea",
            "title": "<script>x</script>",
            "description": "Desc",
            "channels": [channel.pk],
            "next": page,
        },
        **XHR,
    )
    assert response.status_code == 201
    post = Post.objects.get(title="<script>x</script>")
    assert response.json() == {"redirect": page, "url": detail(post)}
    assert [variant.social_account_id for variant in post.platform_posts.all()] == [channel.pk]
    html = http.get(page).content.decode()
    assert f'“&lt;script&gt;x&lt;/script&gt;” was added to Idea. <a href="{detail(post)}"' in html
    assert "<script>x</script>" not in html


@pytest.mark.parametrize("target", ["https://evil.example/", "//evil.example/x", "javascript:alert(1)", ""])
def test_window_never_follows_a_foreign_next(http, workspace, target):
    response = http.post(
        reverse(CREATE), {"workspace": workspace.pk, "title": "Safe", "origin": "idea", "next": target}, **XHR
    )
    assert response.json()["redirect"] == reverse(BOARD) + f"?client={workspace.pk}"


def test_window_error_keeps_the_window_open(http, workspace):
    response = http.post(reverse(CREATE), {"workspace": workspace.pk, "title": "  ", "origin": "idea"}, **XHR)
    assert response.status_code == 400 and response.content.decode() == "Enter a content title."
    assert response["Content-Type"].startswith("text/plain")


def test_window_without_javascript_redirects_with_the_notice(http, workspace):
    response = http.post(reverse(CREATE), {"workspace": workspace.pk, "title": "Plain", "origin": "idea"})
    assert response.status_code == 302 and response.url == reverse(BOARD) + f"?client={workspace.pk}"
    response = http.post(reverse(CREATE), {"workspace": workspace.pk, "title": "", "origin": "idea"})
    assert response.status_code == 302


def test_page_success_opens_the_new_content(http, workspace, channel):
    response = http.post(
        reverse(CREATE),
        {
            "workspace": workspace.pk,
            "origin": "page",
            "title": "From page",
            "stage": "shooting",
            "channels": [channel.pk],
            "publish_at": "2031-02-03T10:15",
        },
    )
    post = Post.objects.get(title="From page")
    assert response.status_code == 302 and response.url == detail(post)
    assert pipeline_stage(post) == "shooting"
    assert post.proposed_publish_at.year == 2031 and post.scheduled_at is None


def test_page_error_shows_the_filled_page_again(http, workspace, channel):
    response = http.post(
        reverse(CREATE),
        {"workspace": workspace.pk, "origin": "page", "title": "Kept", "stage": "review", "description": "Kept text"},
    )
    assert response.status_code == 400
    html = response.content.decode()
    assert "Add a channel" in html and 'value="Kept"' in html and "Kept text" in html
    assert not Post.objects.filter(title="Kept").exists()
    response = http.post(
        reverse(CREATE),
        {"workspace": workspace.pk, "origin": "page", "title": "Bad date", "publish_at": "tomorrow"},
    )
    assert response.status_code == 400 and "valid publication date" in response.content.decode()


def test_create_for_a_foreign_client_is_missing(http, organization):
    foreign = Workspace.objects.create(organization=organization, name="Foreign")
    assert http.post(reverse(CREATE), {"workspace": foreign.pk, "title": "x"}).status_code == 404


def test_quick_idea_announces_the_new_content_with_a_link(http, workspace):
    response = http.post(reverse("jrc_production:quick_idea"), {"workspace": workspace.pk, "title": "Quick"}, **XHR)
    post = Post.objects.get(title="Quick")
    assert response["X-Content-Url"] == detail(post)
    http.post(reverse("jrc_production:quick_idea"), {"workspace": workspace.pk, "title": "Quick 2"})
    html = http.get(reverse(BOARD)).content.decode()
    assert f'“Quick 2” was added to Idea. <a href="{detail(Post.objects.get(title="Quick 2"))}"' in html


# --- New content page ---------------------------------------------------------------------------------


def test_new_content_page_prefills_the_calendar_slot(http, workspace, channel):
    response = http.get(
        reverse(NEW), {"client": workspace.pk, "date": "2030-01-02", "time": "08:15", "account": channel.pk}
    )
    html = response.content.decode()
    assert response.status_code == 200
    assert response.context["fixed"]["workspace"] == workspace
    assert 'value="2030-01-02T08:15"' in html
    assert re.search(rf'value="{channel.pk}" checked', html)
    stages = [value for value, _ in response.context["fixed"]["stages"]]
    assert stages == ["idea", "script", "shooting", "editing", "review", "ready"]


def test_new_content_page_offers_the_client_only_when_unclear(http, workspace, organization, user):
    html = http.get(reverse(NEW)).content.decode()
    assert 'id="new-content-client"' not in html and f'name="workspace" value="{workspace.pk}"' in html
    other = Workspace.objects.create(organization=organization, name="Other")
    WorkspaceMembership.objects.create(workspace=other, user=user, workspace_role="owner")
    html = http.get(reverse(NEW)).content.decode()
    assert 'id="new-content-client"' in html
    html = http.get(reverse(NEW), {"client": other.pk}).content.decode()
    assert 'id="new-content-client"' not in html and f'name="workspace" value="{other.pk}"' in html
    # A client the user does not belong to is never preselected.
    stranger = Workspace.objects.create(organization=organization, name="Stranger")
    assert 'id="new-content-client"' in http.get(reverse(NEW), {"client": stranger.pk}).content.decode()


def test_viewer_gets_no_new_content_page(client, workspace):
    client.force_login(member(workspace, "viewer"))
    assert client.get(reverse(NEW)).status_code == 403


# --- AK1: the old composer -----------------------------------------------------------------------------


def compose(post=None, workspace=None):
    if post is not None:
        return reverse("composer:compose_edit", kwargs={"workspace_id": post.workspace_id, "post_id": post.pk})
    return reverse("composer:compose", kwargs={"workspace_id": workspace.pk})


def test_old_editor_address_opens_the_detail(http, workspace, user, channel):
    post = create_content(workspace, "Old link", user, social_account_ids=[channel.pk])
    assert http.get(compose(post)).url == detail(post)
    assert http.get(compose(post) + f"?account={channel.pk}").url == detail(post, "publish")
    assert http.get(compose(post) + "?action=publish_now").url == detail(post, "publish")


def test_old_new_post_address_opens_the_new_content_page(http, workspace, channel):
    response = http.get(
        compose(workspace=workspace) + f"?scheduled_date=2030-01-02&scheduled_time=08:15&account={channel.pk}"
    )
    assert response.status_code == 302
    assert response.url == (reverse(NEW) + f"?client={workspace.pk}&date=2030-01-02&time=08%3A15&account={channel.pk}")


@pytest.mark.parametrize("role", ["client", "viewer"])
def test_old_editor_refuses_the_same_people_as_before(client, workspace, user, role):
    post = create_content(workspace, "Team content", user)
    client.force_login(member(workspace, role))
    assert client.get(compose(post)).status_code == 403
    assert client.get(compose(workspace=workspace)).status_code == 403


def test_old_editor_refuses_other_authors_without_edit_others(client, workspace, user):
    post = create_content(workspace, "Not mine", user)
    contributor = member(workspace, "contributor")
    client.force_login(contributor)
    assert client.get(compose(post)).status_code == 403
    outsider = User.objects.create_user(email="out@example.invalid", password="x", tos_accepted_at=timezone.now())
    client.force_login(outsider)
    assert client.get(compose(post)).status_code in (403, 404)


def test_no_page_links_to_the_old_editor():
    """Every template link goes to the detail or the New content page (the editor's own page excepted)."""
    root = Path(settings.BASE_DIR) / "templates"
    allowed = {root / "composer" / "compose.html", root / "composer" / "create_landing.html"}
    offenders = []
    for template in root.rglob("*.html"):
        if template in allowed:
            continue
        text = template.read_text()
        if re.search(r"composer:compose(?:_edit)?['\"]", text) or re.search(r"/compose/(?:\?|'|\")", text):
            offenders.append(str(template.relative_to(root)))
    assert offenders == []


def test_calendar_and_queue_links_open_publish(http, workspace, user, channel):
    post = create_content(workspace, "Planned", user, social_account_ids=[channel.pk])
    when = timezone.now() + timedelta(days=1)
    post.platform_posts.update(status="scheduled", scheduled_at=when)
    Post.objects.filter(pk=post.pk).update(scheduled_at=when)
    html = http.get(reverse("calendar:calendar", kwargs={"workspace_id": workspace.pk})).content.decode()
    links = set(re.findall(rf'{re.escape(detail(post))}[^"\']*', html))
    assert links and all(link.endswith("?tab=publish") for link in links), links


# --- detail: back to the board, delete --------------------------------------------------------------------


def test_detail_returns_to_the_board_as_it_was_last_shown(http, workspace, user, channel):
    post = create_content(workspace, "Back", user)
    http.get(reverse(BOARD), {"client": workspace.pk, "channel": channel.pk})
    html = http.get(detail(post)).content.decode()
    assert f'href="{reverse(BOARD)}?client={workspace.pk}&amp;channel={channel.pk}" ' in html
    assert "composer/compose" not in html.replace(f"compose/{post.pk}/delete/", "")


def test_detail_offers_delete_by_the_one_deletion_rule(client, workspace, user, channel):
    post = create_content(workspace, "Delete me", user, social_account_ids=[channel.pk])
    client.force_login(user)
    delete_url = reverse("composer:post_delete", kwargs={"workspace_id": workspace.pk, "post_id": post.pk})
    assert delete_url in client.get(detail(post)).content.decode()
    client.force_login(member(workspace, "viewer"))
    assert delete_url not in client.get(detail(post)).content.decode()
    # Planned content: only someone who may take it out of the plan sees Delete.
    post.platform_posts.update(status="scheduled", scheduled_at=timezone.now() + timedelta(days=1))
    editor = member(workspace, "editor")
    WorkspaceMembership.objects.filter(user=editor).update(custom_role=None)
    client.force_login(editor)
    from apps.members.permissions import content_delete_blocker

    membership = WorkspaceMembership.objects.get(user=editor, workspace=workspace)
    blocked = bool(content_delete_blocker(membership, post, list(post.platform_posts.all())))
    assert (delete_url in client.get(detail(post)).content.decode()) is not blocked
    client.force_login(user)
    response = client.post(delete_url, {"landing": "board"}, **XHR)
    assert response.status_code == 204 and response["X-JRC-Redirect"] == reverse(BOARD) + f"?client={workspace.pk}"
    assert not Post.objects.filter(pk=post.pk).exists()


# --- board and menu -------------------------------------------------------------------------------------


def test_board_has_no_duplicate_new_content_panel(http, workspace):
    html = http.get(reverse(BOARD), {"client": workspace.pk}).content.decode()
    assert "board-new" not in html and "Add to Idea" not in html
    assert f'href="{reverse(NEW)}?client={workspace.pk}" data-new-content-link' in html
    assert "data-open-new-idea" in html and 'id="jrc-new-idea"' in html


def test_menu_new_creates_for_the_current_client(http, workspace, user, organization):
    post = create_content(workspace, "Here", user)
    html = http.get(detail(post)).content.decode()
    assert f'href="{reverse(NEW)}?client={workspace.pk}" data-new-content-link' in html
    window = html[html.index('id="jrc-new-idea"') :]
    assert f'name="workspace" value="{workspace.pk}"' in window
    # On the board for all clients the window asks which client.
    other = Workspace.objects.create(organization=organization, name="Other")
    WorkspaceMembership.objects.create(workspace=other, user=user, workspace_role="owner")
    html = http.get(reverse(BOARD), {"client": ""}).content.decode()
    assert 'id="new-idea-client"' in html


def test_viewer_sees_no_new_menu_or_window(client, workspace):
    client.force_login(member(workspace, "viewer"))
    html = client.get(reverse(BOARD)).content.decode()
    assert (
        'class="sidebar-new-wrap' not in html and 'id="jrc-new-idea"' not in html and "data-open-new-idea" not in html
    )


# --- review P2-B03: window client on New content, slot times, no detour through the composer --------------


def test_window_on_the_new_content_page_creates_for_the_client_the_page_shows(http, workspace, organization, user):
    other = Workspace.objects.create(organization=organization, name="Other client")
    WorkspaceMembership.objects.create(workspace=other, user=user, workspace_role="owner")
    # The last workspace the user opened is ``workspace``; the page shows ``other``.
    http.get(detail(create_content(workspace, "Opened last", user)))
    html = http.get(reverse(NEW), {"client": other.pk}).content.decode()
    window = html[html.index('id="jrc-new-idea"') :]
    assert f'name="workspace" value="{other.pk}"' in window
    assert f'name="workspace" value="{workspace.pk}"' not in window
    assert f'href="{reverse(NEW)}?client={other.pk}" data-new-content-link' in html
    # Without a clear client on the page the window asks too.
    html = http.get(reverse(NEW)).content.decode()
    assert 'id="new-idea-client"' in html[html.index('id="jrc-new-idea"') :]


@pytest.mark.parametrize(
    ("time", "expected"),
    [
        ("8:00", "2030-01-02T08:00"),
        ("08:00", "2030-01-02T08:00"),
        ("23:30", "2030-01-02T23:30"),
        ("", "2030-01-02T09:00"),
    ],
)
def test_new_content_accepts_the_slot_hour_with_or_without_a_leading_zero(http, workspace, time, expected):
    html = http.get(reverse(NEW), {"client": workspace.pk, "date": "2030-01-02", "time": time}).content.decode()
    assert f'value="{expected}"' in html


@pytest.mark.parametrize(("date", "time"), [("2030-13-02", "08:00"), ("2030-01-02", "25:00"), ("x", "8:00")])
def test_new_content_ignores_an_invalid_slot(http, workspace, date, time):
    response = http.get(reverse(NEW), {"client": workspace.pk, "date": date, "time": time})
    assert response.status_code == 200 and response.context["values"]["publish_at"] == ""


@pytest.mark.parametrize("view", ["day", "week"])
def test_organization_calendar_slots_send_a_two_digit_hour(http, workspace, organization, user, view):
    from apps.members.models import OrgMembership

    OrgMembership.objects.get_or_create(organization=organization, user=user, defaults={"org_role": "owner"})
    html = http.get(
        reverse("organizations:cross_workspace_calendar"), {"view": view, "date": "2030-01-02"}
    ).content.decode()
    times = re.findall(r"new/\?client=[^\"]*?&(?:amp;)?time=([0-9:]+)\"", html)
    assert times and all(re.fullmatch(r"\d{2}:\d{2}", value) for value in times), times[:5]
    assert "08:00" in times


def test_composer_views_never_send_to_the_old_editor(http, workspace, user, channel):
    """Save, clone, template and idea redirects name the detail / New content directly (no detour)."""
    from apps.composer.models import PostTemplate
    from apps.composer.views import content_page_url

    post = create_content(workspace, "Saved", user, social_account_ids=[channel.pk])
    old_editor = re.compile(r"/compose/(?:[0-9a-f-]{36}/)?(?:\?|$)")
    save = http.post(
        reverse("composer:save_post_edit", kwargs={"workspace_id": workspace.pk, "post_id": post.pk}),
        {"action": "save_draft", "title": "Saved", "caption": "", "selected_accounts": str(channel.pk)},
    )
    assert save.status_code == 302 and save.url == detail(post)
    clone = http.post(reverse("composer:clone_post", kwargs={"workspace_id": workspace.pk, "post_id": post.pk}))
    assert clone.status_code == 302 and "/content/" in clone.url and not old_editor.search(clone.url)
    template = PostTemplate.objects.create(workspace=workspace, name="T", template_data={"caption": "x"})
    use = http.get(reverse("composer:use_template", kwargs={"workspace_id": workspace.pk, "template_id": template.pk}))
    assert use.status_code == 302 and use.url == reverse(NEW) + f"?client={workspace.pk}"
    saved_as = http.post(
        reverse("composer:save_as_template", kwargs={"workspace_id": workspace.pk, "post_id": post.pk}),
        {"name": "From post"},
    )
    assert saved_as.status_code in (302, 204) and not old_editor.search(saved_as.get("Location", ""))
    assert content_page_url(workspace.pk, post.pk, {"account": str(channel.pk)}) == detail(post, "publish")


def test_no_composer_view_names_the_old_editor_as_target():
    """Every composer redirect goes through ``content_page_url``; only it names the editor's pages."""
    from inspect import getsource

    import apps.composer.views as views
    import apps.onboarding.checklist as checklist

    source = getsource(views)
    helper = getsource(views.content_page_url)
    assert source.count('"composer:compose_edit"') == helper.count('"composer:compose_edit"') == 1
    assert source.count('"composer:compose"') == helper.count('"composer:compose"') == 1
    assert "composer:compose" not in getsource(checklist)
