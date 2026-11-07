# JR Content: přidáno 2026-11-07 (blok P1-B38), AGPL-3.0.
"""Renaming content, the message after a deletion, rejected requests in the app layout (P1-B38).

Expectations are written by hand:

* Renaming is editing: the author with create rights, anyone with edit-others rights (owner, manager,
  editor). Contributor only their own; client, viewer, non-member and a role without create rights
  never. It is written to the History tab, and where the title goes out (the copy has no own title)
  it sends an approved copy back to review in the two modes that require approval.
* A deletion answers with a message; the composer is led to the content board of that client with
  the message flashed there. A rejected page request (400/403/404/405) is answered in the app layout,
  a fragment request keeps the plain text.
"""

import uuid

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.composer.models import PlatformPost, Post
from apps.jrc_channels.services import create_manual_channel
from apps.jrc_production.models import ContentRename
from apps.members.models import CustomRole, OrgMembership, WorkspaceMembership
from apps.workspaces.models import Workspace

pytestmark = pytest.mark.django_db

HTML = {"HTTP_ACCEPT": "text/html,application/xhtml+xml"}


@pytest.fixture
def make(organization):
    counter = {"n": 0}

    def build(mode="required_internal_and_client", role="owner", own=True):
        counter["n"] += 1
        n = counter["n"]
        workspace = Workspace.objects.create(
            organization=organization, name=f"B38 {mode} {role} {n}", approval_workflow_mode=mode
        )
        account = create_manual_channel(workspace, "custom", f"B38 channel {n}")
        actor = User.objects.create_user(
            email=f"b38-{role}-{n}@example.invalid", tos_accepted_at=timezone.now(), name=f"B38 {role}"
        )
        OrgMembership.objects.create(
            user=actor, organization=organization, org_role="owner" if role == "owner" else "member"
        )
        WorkspaceMembership.objects.create(user=actor, workspace=workspace, workspace_role=role)
        other = User.objects.create_user(email=f"b38-author-{n}@example.invalid", tos_accepted_at=timezone.now())
        WorkspaceMembership.objects.create(user=other, workspace=workspace, workspace_role="owner")
        post = Post.objects.create(
            workspace=workspace, author=actor if own else other, title="Old name", caption="Base caption"
        )
        copy = PlatformPost.objects.create(post=post, social_account=account, status="approved")
        client = Client()
        client.force_login(actor)
        return workspace, post, copy, actor, client

    return build


def _rename(client, post, title):
    url = reverse("jrc_production:rename", args=[post.workspace_id, post.pk])
    return client.post(url, {"title": title})


# ---- AK1 rename -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("role", "own", "allowed"),
    [
        ("owner", True, True),
        ("owner", False, True),
        ("manager", False, True),
        ("editor", False, True),
        ("editor", True, True),
        ("contributor", True, True),
        ("contributor", False, False),
        ("client", True, False),
        ("client", False, False),
        ("viewer", False, False),
    ],
)
def test_rename_follows_edit_rights(make, role, own, allowed):
    _, post, _, _, client = make(role=role, own=own)
    response = _rename(client, post, "  New name  ")
    post.refresh_from_db()
    if allowed:
        assert response.status_code == 302
        assert post.title == "New name"
        row = ContentRename.objects.get(post=post)
        assert (row.old_title, row.new_title) == ("Old name", "New name")
    else:
        assert response.status_code in {403, 404}
        assert post.title == "Old name"
        assert not ContentRename.objects.exists()


def test_rename_by_non_member_and_anonymous_changes_nothing(make):
    _, post, _, _, _ = make()
    stranger = User.objects.create_user(email="b38-stranger@example.invalid", tos_accepted_at=timezone.now())
    client = Client()
    client.force_login(stranger)
    assert _rename(client, post, "Hacked").status_code in {403, 404}
    anonymous = _rename(Client(), post, "Hacked")
    assert anonymous.status_code == 302
    assert "login" in anonymous["Location"]
    post.refresh_from_db()
    assert post.title == "Old name"


def test_role_without_create_rights_cannot_rename_even_as_author(make):
    workspace, post, _, actor, client = make(role="editor")
    membership = WorkspaceMembership.objects.get(workspace=workspace, user=actor)
    membership.custom_role = CustomRole.objects.create(
        organization=workspace.organization, name="No writes", permissions={"edit_others_posts": True}
    )
    membership.save()
    assert _rename(client, post, "New").status_code == 403
    post.refresh_from_db()
    assert post.title == "Old name"


def test_rename_get_is_not_a_write(make):
    _, post, _, _, client = make()
    response = client.get(reverse("jrc_production:rename", args=[post.workspace_id, post.pk]), **HTML)
    assert response.status_code == 405
    assert b'data-rejected-page="405"' in response.content


@pytest.mark.parametrize("value", ["", "   ", "x" * 256])
def test_rename_rejects_empty_and_too_long_titles(make, value):
    _, post, _, _, client = make()
    response = _rename(client, post, value)
    assert response.status_code == 302
    post.refresh_from_db()
    assert post.title == "Old name"
    assert not ContentRename.objects.exists()


def test_rename_to_the_same_title_writes_no_history_and_keeps_approval(make):
    _, post, copy, _, client = make()
    _rename(client, post, " Old name ")
    copy.refresh_from_db()
    assert copy.status == "approved"
    assert not ContentRename.objects.exists()


def test_rename_appears_in_history_with_both_names_and_the_page_shows_the_rename_form(make):
    workspace, post, _, _, client = make()
    _rename(client, post, "Brand new")
    url = reverse("jrc_production:content_detail", args=[workspace.pk, post.pk])
    history = client.get(url + "?tab=history").content.decode()
    assert "Renamed from “Old name” to “Brand new”" in history
    page = client.get(url).content.decode()
    assert "Brand new" in page
    assert 'name="title"' in page
    assert reverse("jrc_production:rename", args=[workspace.pk, post.pk]) in page


def test_client_does_not_see_the_rename_form(make):
    workspace, post, _, _, client = make(role="client", own=False)
    page = client.get(reverse("jrc_production:content_detail", args=[workspace.pk, post.pk])).content.decode()
    assert "Save title" not in page


@pytest.mark.parametrize(
    ("mode", "returns"),
    [("none", False), ("optional", False), ("required_internal", True), ("required_internal_and_client", True)],
)
def test_rename_returns_approved_copy_to_review_only_where_approval_is_required(make, mode, returns):
    _, post, copy, _, client = make(mode=mode)
    _rename(client, post, "Another title")
    copy.refresh_from_db()
    assert copy.status == ("pending_review" if returns else "approved")


def test_rename_leaves_a_copy_with_its_own_title_approved(make):
    _, post, copy, _, client = make()
    copy.platform_specific_title = "Own title for this channel"
    copy.save(update_fields=["platform_specific_title"])
    _rename(client, post, "Another title")
    copy.refresh_from_db()
    assert copy.status == "approved"


def test_titles_written_by_composer_autosave_and_rest_reach_history(make):
    from apps.composer.services import record_title_change

    _, post, _, actor, _ = make()
    old = post.title
    post.title = "Composer new"
    post.save()
    record_title_change(post, old, actor)
    record_title_change(post, "Composer new", actor)  # unchanged: no second row
    record_title_change(post, None, actor)  # new content: no row
    assert list(ContentRename.objects.values_list("old_title", "new_title")) == [("Old name", "Composer new")]


# ---- AK2 deletion message -------------------------------------------------------------------------


def test_composer_deletion_leads_to_the_board_of_that_client_with_a_message(make):
    workspace, post, _, _, client = make()
    url = reverse("composer:post_delete", args=[workspace.pk, post.pk])
    response = client.post(url, {"landing": "board"}, HTTP_HX_REQUEST="true")
    assert response.status_code == 204
    assert response["X-JRC-Message"] == "Content deleted."
    assert response["X-JRC-Redirect"] == reverse("jrc_production:board") + f"?client={workspace.pk}"
    assert not Post.objects.filter(pk=post.pk).exists()
    board = client.get(response["X-JRC-Redirect"]).content.decode()
    assert "Content deleted." in board
    assert "Content deleted." not in client.get(response["X-JRC-Redirect"]).content.decode()  # shown once


def test_deletion_from_a_list_stays_in_place_and_flashes_nothing(make):
    workspace, post, _, _, client = make()
    response = client.post(reverse("composer:post_delete", args=[workspace.pk, post.pk]))
    assert response.status_code == 204
    assert response["X-JRC-Message"] == "Content deleted."
    assert "X-JRC-Redirect" not in response
    board = client.get(reverse("jrc_production:board")).content.decode()
    assert "Content deleted." not in board


def test_channel_removal_says_so_and_never_leaves_the_page(make):
    workspace, post, copy, _, client = make()
    url = reverse("composer:post_delete", args=[workspace.pk, post.pk]) + f"?account={copy.social_account_id}"
    response = client.post(url, {"landing": "board"})
    assert response.status_code == 204
    assert response["X-JRC-Message"] == "Channel removed."
    assert "X-JRC-Redirect" not in response
    assert Post.objects.filter(pk=post.pk).exists()


def test_the_landing_is_built_by_the_server_never_taken_from_the_request(make):
    workspace, post, _, _, client = make()
    url = reverse("composer:post_delete", args=[workspace.pk, post.pk])
    response = client.post(url, {"landing": "https://evil.example/", "next": "https://evil.example/"})
    assert "X-JRC-Redirect" not in response


def test_calendar_bulk_delete_names_the_result(make):
    workspace, post, copy, _, client = make()
    copy.status = "draft"
    copy.save(update_fields=["status"])
    url = reverse("calendar:bulk_platform_action", args=[workspace.pk])
    response = client.post(url, {"action": "delete", "platform_post_ids": [str(copy.pk)]})
    assert response.status_code == 200
    assert response.json()["message"] == "Channel removed."


def test_compose_page_delete_button_uses_the_server_landing(make):
    workspace, post, _, _, client = make()
    page = client.get(reverse("composer:compose_edit", args=[workspace.pk, post.pk])).content.decode()
    assert "landing=board" in page
    assert "X-JRC-Redirect" in page


# ---- AK3 rejected requests in the layout ----------------------------------------------------------


def _foreign_board(client):
    return reverse("jrc_production:board") + f"?client={uuid.uuid4()}"


def test_dashboard_with_a_foreign_client_is_a_layout_page_with_the_reason(make):
    _, _, _, _, client = make()
    response = client.get(_foreign_board(client), **HTML)
    body = response.content.decode()
    assert response.status_code == 400
    assert 'data-rejected-page="400"' in body
    assert "Choose a client you have access to." in body
    assert "<html" in body.lower()
    assert reverse("jrc_production:board") in body  # the way back


def test_fragment_and_script_requests_keep_the_plain_answer(make):
    _, _, _, _, client = make()
    url = _foreign_board(client)
    plain = client.get(url)
    assert plain.status_code == 400
    assert plain.content == b"Choose a client you have access to."
    assert client.get(url, HTTP_ACCEPT="text/html", HTTP_HX_REQUEST="true").content == plain.content
    assert client.get(url, HTTP_ACCEPT="text/html", HTTP_X_REQUESTED_WITH="XMLHttpRequest").content == plain.content
    assert client.get(url, HTTP_ACCEPT="text/html", HTTP_SEC_FETCH_DEST="iframe").content == plain.content


def test_forbidden_and_missing_pages_stay_in_the_layout(make):
    workspace, post, _, _, client = make(role="client", own=False)
    forbidden = client.post(reverse("composer:post_delete", args=[workspace.pk, post.pk]), **HTML)
    assert forbidden.status_code == 403
    assert b"<html" in forbidden.content.lower()
    missing = client.get(f"/jrc/workspace/{workspace.pk}/content/{uuid.uuid4()}/", **HTML)
    assert missing.status_code == 404
    assert b"<html" in missing.content.lower()


def test_rejected_page_escapes_the_message_and_keeps_headers(make):
    from django.http import HttpResponse
    from django.test import RequestFactory

    from apps.jrc_ui.middleware import RejectedPageMiddleware

    _, _, _, actor, _ = make()
    request = RequestFactory().get("/x/", **HTML)
    request.user = actor
    from django.contrib.messages.storage.fallback import FallbackStorage

    request.session = {}
    request._messages = FallbackStorage(request)
    response = RejectedPageMiddleware(
        lambda r: HttpResponse("<script>alert(1)</script>", status=400, content_type="text/plain")
    )(request)
    assert b"<script>alert(1)</script>" not in response.content
    assert b"&lt;script&gt;" in response.content
    allowed = RejectedPageMiddleware(lambda r: _not_allowed())(request)
    assert allowed.status_code == 405
    assert allowed["Allow"] == "POST"


def _not_allowed():
    from django.http import HttpResponseNotAllowed

    return HttpResponseNotAllowed(["POST"])


def test_successful_pages_and_non_bare_errors_are_untouched(make):
    from django.http import HttpResponse, JsonResponse
    from django.test import RequestFactory

    from apps.jrc_ui.middleware import RejectedPageMiddleware

    request = RequestFactory().get("/x/", **HTML)
    ok = HttpResponse("fine")
    assert RejectedPageMiddleware(lambda r: ok)(request) is ok
    api = JsonResponse({"error": "nope"}, status=400)
    assert RejectedPageMiddleware(lambda r: api)(request) is api
    full = HttpResponse("<!DOCTYPE html><html><body>own page</body></html>", status=404)
    assert RejectedPageMiddleware(lambda r: full)(request) is full
    large = HttpResponse("x" * 5000, status=400, content_type="text/plain")
    assert RejectedPageMiddleware(lambda r: large)(request) is large
    limited = HttpResponse("slow down", status=429, content_type="text/plain")
    assert RejectedPageMiddleware(lambda r: limited)(request) is limited


@pytest.mark.parametrize("role", ["client", "viewer"])
def test_permission_is_checked_before_the_title_is_validated(make, role):
    _, post, _, _, client = make(role=role, own=False)
    assert _rename(client, post, "").status_code == 403
