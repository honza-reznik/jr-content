"""P1-B07: compact clickable cards, column order, remembered client filter and quick ideas."""

import re
from datetime import timedelta

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.approvals.models import PostComment
from apps.composer.models import Post
from apps.jrc_channels.services import create_manual_channel
from apps.jrc_production.services import create_content, move_content, pipeline_stage
from apps.members.models import WorkspaceMembership
from apps.workspaces.models import Workspace

pytestmark = pytest.mark.django_db

BOARD = "jrc_production:board"
IDEA = "jrc_production:quick_idea"
XHR = {"HTTP_X_REQUESTED_WITH": "XMLHttpRequest"}


@pytest.fixture
def http(client, user):
    client.force_login(user)
    return client


def only_workspace(user, workspace):
    # Signup also creates a personal workspace for the user.
    WorkspaceMembership.objects.filter(user=user).exclude(workspace=workspace).delete()


def card_html(html, post):
    start = html.index(f'data-post="{post.pk}"')
    return html[start : html.index("</article>", start)]


def column_order(response, stage):
    column = next(c for c in response.context["columns"] if c["value"] == stage)
    return [card["post"].title for card in column["cards"]]


def detail_url(post):
    return reverse("jrc_production:content_detail", kwargs={"workspace_id": post.workspace_id, "post_id": post.pk})


def test_whole_card_links_to_detail_and_move_stays_separate(http, workspace, user):
    post = create_content(workspace, "Studio tour", user)
    html = http.get(reverse(BOARD)).content.decode()
    card = card_html(html, post)
    assert f'<a class="board-card-link" href="{detail_url(post)}"' in card
    # The stretched link covers the card; the move menu sits above it as its own control.
    assert (
        ".board-card-link::after" in html and ".board-move { margin-left:auto; position:relative; z-index:1; }" in html
    )
    assert '<details class="board-move"><summary aria-label="Move “Studio tour”">Move…</summary>' in card
    assert 'name="expected_stage" value="idea"' in card


def test_compact_card_shows_only_useful_details(http, workspace, organization, user):
    channel = create_manual_channel(workspace, "instagram", "Ella Instagram")
    with_channel = create_content(workspace, "With channel", user, social_account_ids=[channel.pk])
    without = create_content(workspace, "Without channel", user)
    proposed = timezone.now() + timedelta(days=3)
    Post.objects.filter(pk=without.pk).update(proposed_publish_at=proposed)
    PostComment.objects.create(post=with_channel, author=user, body="Team note", visibility="internal")
    root = PostComment.objects.create(post=with_channel, author=user, body="For Ella", visibility="external")
    PostComment.objects.create(post=with_channel, author=user, body="Reply", visibility="external", parent_comment=root)
    PostComment.objects.create(post=with_channel, author=user, body="Gone", deleted_at=timezone.now())
    other = Workspace.objects.create(organization=organization, name="Other client")
    WorkspaceMembership.objects.create(workspace=other, user=user, workspace_role="owner")
    create_content(other, "Other client idea", user)

    html = http.get(reverse(BOARD), {"client": ""}).content.decode()
    first, second = card_html(html, with_channel), card_html(html, without)
    # cards of several clients on the board: the client name helps
    assert '<div class="board-grid show-clients">' in html
    assert '<span class="board-client">Production test</span>' in first
    assert ">Ella Instagram</span>" in first and "No channel" not in first
    assert "board-channel board-channel-none" in second and ">No channel</span>" in second
    assert '3<span class="board-sr"> comments</span>' in first
    assert "board-comments" not in second
    assert "Proposed " in second and "<time" not in first

    filtered = http.get(reverse(BOARD), {"client": workspace.pk}).content.decode()
    assert '<div class="board-grid">' in filtered and "board-grid show-clients" not in filtered


def test_clients_count_only_comments_they_may_read(client, workspace, user):
    post = create_content(workspace, "Client view", user)
    PostComment.objects.create(post=post, author=user, body="Internal", visibility="internal")
    PostComment.objects.create(post=post, author=user, body="External", visibility="external")
    ella = User.objects.create_user(
        email="ella-board@example.invalid", password="test", name="Ella", tos_accepted_at=timezone.now()
    )
    WorkspaceMembership.objects.create(workspace=workspace, user=ella, workspace_role="client")
    client.force_login(ella)
    response = client.get(reverse(BOARD))
    card = card_html(response.content.decode(), post)
    assert '1<span class="board-sr"> comment</span>' in card
    client.force_login(user)
    card = card_html(client.get(reverse(BOARD), {"client": workspace.pk}).content.decode(), post)
    assert '2<span class="board-sr"> comments</span>' in card


def test_single_client_member_does_not_see_client_name(http, workspace, user):
    only_workspace(user, workspace)
    post = create_content(workspace, "One client", user)
    response = http.get(reverse(BOARD))
    assert response.context["show_client"] is False
    assert f'data-client="{workspace.pk}"' in card_html(response.content.decode(), post)
    assert '<div class="board-grid">' in response.content.decode()


def test_client_names_follow_cards_on_screen_not_memberships(http, workspace, organization, user):
    # Member of two clients, but only one of them has content (also with "All clients"): no names.
    other = Workspace.objects.create(organization=organization, name="Empty client")
    WorkspaceMembership.objects.create(workspace=other, user=user, workspace_role="owner")
    create_content(workspace, "Only client with content", user)
    response = http.get(reverse(BOARD), {"client": ""})
    assert response.context["show_client"] is False
    assert "board-grid show-clients" not in response.content.decode()
    create_content(other, "Now two clients", user)
    response = http.get(reverse(BOARD), {"client": ""})
    assert response.context["show_client"] is True
    # A channel filter that leaves one client's cards hides the names again.
    channel = create_manual_channel(other, "instagram", "Other IG")
    create_content(other, "Other with channel", user, social_account_ids=[channel.pk])
    assert http.get(reverse(BOARD), {"client": "", "channel": channel.pk}).context["show_client"] is False


def test_columns_sort_by_publication_date_then_latest_change(http, workspace, user):
    now = timezone.now()
    old = create_content(workspace, "Old undated", user)
    new = create_content(workspace, "New undated", user)
    late = create_content(workspace, "Dated later", user)
    soon = create_content(workspace, "Dated soon", user)
    Post.objects.filter(pk=old.pk).update(updated_at=now - timedelta(days=5))
    Post.objects.filter(pk=new.pk).update(updated_at=now - timedelta(days=1))
    Post.objects.filter(pk=late.pk).update(proposed_publish_at=now + timedelta(days=9), updated_at=now)
    Post.objects.filter(pk=soon.pk).update(
        proposed_publish_at=now + timedelta(days=2), updated_at=now - timedelta(days=9)
    )
    response = http.get(reverse(BOARD), {"client": workspace.pk})
    assert column_order(response, "idea") == ["Dated soon", "Dated later", "New undated", "Old undated"]
    html = response.content.decode()
    assert html.index(f'data-post="{soon.pk}"') < html.index(f'data-post="{new.pk}"')
    assert 'data-dated="1"' in card_html(html, soon) and 'data-dated="0"' in card_html(html, new)


def test_published_column_shows_newest_first(http, workspace, user):
    from apps.jrc_production.views import _sort_cards

    now = timezone.now()
    cards = [
        {"post": Post(title="a", updated_at=now), "date": now - timedelta(days=3)},
        {"post": Post(title="b", updated_at=now), "date": now - timedelta(days=1)},
        {"post": Post(title="c", updated_at=now), "date": None},
    ]
    assert [c["post"].title for c in _sort_cards(list(cards), newest_first=True)] == ["b", "a", "c"]
    assert [c["post"].title for c in _sort_cards(list(cards))] == ["a", "b", "c"]


def test_board_remembers_last_client_in_session(http, workspace, organization, user):
    other = Workspace.objects.create(organization=organization, name="Other client")
    WorkspaceMembership.objects.create(workspace=other, user=user, workspace_role="owner")
    create_content(workspace, "Mine", user)
    create_content(other, "Theirs", user)
    url = reverse(BOARD)
    assert http.get(url).context["selected_client"] == ""
    http.get(url, {"client": other.pk})
    remembered = http.get(url)
    assert remembered.context["selected_client"] == str(other.pk)
    assert [c["post"].title for col in remembered.context["columns"] for c in col["cards"]] == ["Theirs"]
    assert f'<option value="{other.pk}" selected>' in remembered.content.decode()
    # "Clear" and "All clients" are explicit choices and are remembered too.
    assert f'href="{url}?client="' in remembered.content.decode()
    http.get(url, {"client": ""})
    assert http.get(url).context["selected_client"] == ""
    # A remembered client the user lost access to is ignored, never an error.
    http.get(url, {"client": other.pk})
    WorkspaceMembership.objects.filter(workspace=other, user=user).delete()
    response = http.get(url)
    assert response.status_code == 200 and response.context["selected_client"] == ""


def test_invalid_filter_is_not_remembered(http, workspace, organization, user):
    hidden = Workspace.objects.create(organization=organization, name="Hidden")
    http.get(reverse(BOARD), {"client": workspace.pk})
    assert http.get(reverse(BOARD), {"client": hidden.pk}).status_code == 400
    assert http.get(reverse(BOARD)).context["selected_client"] == str(workspace.pk)


def test_three_quick_ideas_in_a_row_stay_on_the_board(http, workspace, user):
    channel = create_manual_channel(workspace, "instagram", "Ella Instagram")
    for title in ("First idea", "Second idea", "Third idea"):
        response = http.post(
            reverse(IDEA),
            {"workspace": workspace.pk, "title": f"  {title} ", "board_client": workspace.pk, "board_channel": ""},
            **XHR,
        )
        assert response.status_code == 201
        assert b"Shotlist 0/1 done" in response.content
        html = response.content.decode()
        assert html.strip().startswith('<article class="board-card"') and title in html
        assert 'data-dated="0"' in html and ">No channel</span>" in html and "Move…" in html
        assert response["X-Board-Match"] == "1"
    posts = Post.objects.filter(workspace=workspace).order_by("created_at")
    assert [p.title for p in posts] == ["First idea", "Second idea", "Third idea"]
    for post in posts:
        assert pipeline_stage(post) == "idea"
        assert not post.platform_posts.exists()
        assert post.scheduled_at is None and post.proposed_publish_at is None
    assert channel.platform_posts.count() == 0


def test_quick_idea_without_javascript_redirects_back_to_board(http, workspace):
    response = http.post(reverse(IDEA), {"workspace": workspace.pk, "title": "No JS idea", "board_client": ""})
    assert response.status_code == 302
    assert response.url == reverse(BOARD) + "?client=&channel="
    assert Post.objects.filter(title="No JS idea").exists()


@pytest.mark.parametrize("title", ["", "   ", "x" * 256])
def test_quick_idea_rejects_invalid_title(http, workspace, title):
    response = http.post(reverse(IDEA), {"workspace": workspace.pk, "title": title}, **XHR)
    assert response.status_code == 400
    assert response["Content-Type"].startswith("text/plain")
    assert not Post.objects.exists()
    response = http.post(reverse(IDEA), {"workspace": workspace.pk, "title": title}, follow=True)
    assert response.status_code == 200 and not Post.objects.exists()


@pytest.mark.parametrize("role,status", [("client", 201), ("viewer", 403), ("outsider", 404)])
def test_quick_idea_permissions(http, workspace, user, role, status):
    if role == "outsider":
        WorkspaceMembership.objects.filter(workspace=workspace, user=user).delete()
    else:
        WorkspaceMembership.objects.filter(workspace=workspace, user=user).update(workspace_role=role)
    response = http.post(reverse(IDEA), {"workspace": workspace.pk, "title": "Client idea"}, **XHR)
    assert response.status_code == status
    assert Post.objects.filter(title="Client idea").exists() is (status == 201)


def test_client_sees_quick_idea_for_own_workspace_only(client, workspace, organization, user):
    ella = User.objects.create_user(
        email="ella-idea@example.invalid", password="test", name="Ella", tos_accepted_at=timezone.now()
    )
    WorkspaceMembership.objects.create(workspace=workspace, user=ella, workspace_role="client")
    only_workspace(ella, workspace)
    foreign = Workspace.objects.create(organization=organization, name="Foreign")
    client.force_login(ella)
    html = client.get(reverse(BOARD)).content.decode()
    form = html.split('id="quick-idea"', 1)[1].split("</form>", 1)[0]
    assert f'<input type="hidden" name="workspace" value="{workspace.pk}" data-client-name="Production test">' in form
    assert 'placeholder="Idea…"' in form
    assert client.post(reverse(IDEA), {"workspace": foreign.pk, "title": "Leak"}, **XHR).status_code == 404
    assert not Post.objects.filter(title="Leak").exists()


def test_viewer_has_no_quick_idea(http, workspace, user):
    only_workspace(user, workspace)
    WorkspaceMembership.objects.filter(workspace=workspace, user=user).update(workspace_role="viewer")
    html = http.get(reverse(BOARD)).content.decode()
    assert 'id="quick-idea"' not in html and "+ Idea" not in html


def test_quick_idea_defaults_to_filter_then_last_used_client(http, workspace, organization, user):
    other = Workspace.objects.create(organization=organization, name="Zeta client")
    WorkspaceMembership.objects.create(workspace=other, user=user, workspace_role="owner")
    url = reverse(BOARD)

    def selected_idea_client(**query):
        html = http.get(url, query).content.decode()
        form = html.split('id="quick-idea"', 1)[1].split("</form>", 1)[0]
        return re.search(r'<option value="([^"]+)" selected>', form).group(1)

    assert selected_idea_client(client=other.pk) == str(other.pk)
    assert selected_idea_client(client=workspace.pk) == str(workspace.pk)
    http.post(reverse(IDEA), {"workspace": other.pk, "title": "For Zeta"}, **XHR)
    assert selected_idea_client(client="") == str(other.pk)
    assert selected_idea_client(client=workspace.pk) == str(workspace.pk)


def test_quick_idea_card_keeps_only_valid_board_filters(http, workspace, organization, user):
    hidden = Workspace.objects.create(organization=organization, name="Hidden")
    response = http.post(
        reverse(IDEA),
        {"workspace": workspace.pk, "title": "Filtered", "board_client": hidden.pk, "board_channel": 'x"><script>'},
        **XHR,
    )
    html = response.content.decode()
    assert '<input type="hidden" name="client" value="">' in html
    assert '<input type="hidden" name="channel" value="">' in html
    assert "<script>" not in html


def test_quick_idea_requires_post_login_and_csrf(workspace, user):
    anonymous = Client()
    assert anonymous.post(reverse(IDEA), {"workspace": workspace.pk, "title": "Anon"}).status_code == 302
    secure = Client(enforce_csrf_checks=True)
    secure.force_login(user)
    assert secure.get(reverse(IDEA)).status_code == 405
    assert secure.post(reverse(IDEA), {"workspace": workspace.pk, "title": "No CSRF"}, **XHR).status_code == 403
    assert not Post.objects.exists()


def test_moved_quick_idea_keeps_working(http, workspace, user):
    http.post(reverse(IDEA), {"workspace": workspace.pk, "title": "Grows up"}, **XHR)
    post = Post.objects.get(title="Grows up")
    move_content(post, "script", user, expected_stage="idea")
    response = http.get(reverse(BOARD), {"client": workspace.pk})
    assert column_order(response, "script") == ["Grows up"]


@pytest.mark.parametrize(
    "board,match",
    [
        ({"board_client": "", "board_channel": ""}, "1"),
        ({"board_client": "same", "board_channel": "none"}, "1"),
        ({"board_client": "other", "board_channel": ""}, "0"),
        ({"board_client": "same", "board_channel": "channel"}, "0"),
    ],
)
def test_quick_idea_reports_whether_active_filters_show_it(http, workspace, organization, user, board, match):
    other = Workspace.objects.create(organization=organization, name="Other client")
    WorkspaceMembership.objects.create(workspace=other, user=user, workspace_role="owner")
    channel = create_manual_channel(workspace, "instagram", "Ella Instagram")
    values = {"same": str(workspace.pk), "other": str(other.pk), "channel": str(channel.pk)}
    data = {key: values.get(value, value) for key, value in board.items()}
    response = http.post(reverse(IDEA), {"workspace": workspace.pk, "title": "Filtered idea", **data}, **XHR)
    assert response.status_code == 201
    assert response["X-Board-Match"] == match
    # Saved either way, always without channel.
    assert not Post.objects.get(title="Filtered idea").platform_posts.exists()
