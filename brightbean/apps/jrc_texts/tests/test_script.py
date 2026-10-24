"""Script HTTP workflow against the real version services and PostgreSQL locks."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from django.db import close_old_connections
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.composer.models import Post
from apps.jrc_texts.models import TextDocument, TextVersion
from apps.members.models import WorkspaceMembership
from apps.workspaces.models import Workspace

pytestmark = pytest.mark.django_db


def content(text):
    return {"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": text}]}]}


@pytest.fixture
def post(workspace, user):
    return Post.objects.create(workspace=workspace, author=user, title="Script workflow")


def url(post):
    return reverse("jrc_texts:script", kwargs={"workspace_id": post.workspace_id, "post_id": post.pk})


@pytest.fixture
def http(client, user):
    client.force_login(user)
    return client


def send(http, post, action, **data):
    return http.post(url(post), {"action": action, **data}, content_type="application/json")


def draft_request(state, text="Updated outline"):
    return {"version": state["draft"]["id"], "revision": state["draft"]["revision"], "content": content(text)}


def test_read_never_creates_document_and_detail_loads_local_bundle(http, post):
    response = http.get(url(post))
    assert response.status_code == 200
    assert response.json()["versions"] == []
    assert response.json()["can_edit"] is True
    assert "no-store" in response["Cache-Control"]
    assert not TextDocument.objects.filter(post=post).exists()
    detail = http.get(reverse("jrc_production:content_detail", args=[post.workspace_id, post.pk]))
    assert b"jrc_texts/dist/script.js" in detail.content
    assert b"Script details will appear here" not in detail.content
    assert b"Edit text" in detail.content


def test_edit_autosave_resume_save_history_and_discard(http, post):
    state = send(http, post, "edit", base=None).json()
    assert state["draft"]["own"]
    assert len(state["versions"]) == 1
    base = state["shown"]
    first = draft_request(state)
    response = send(http, post, "autosave", **first)
    assert response.status_code == 200
    state = response.json()
    assert state["draft"]["revision"] == 1
    assert state["shown"]["content"] == base["content"]
    assert len(state["versions"]) == 1
    # A page refresh offers an explicit resume, retaining only the owner's draft content.
    assert http.get(url(post)).json()["draft"]["content"] == content("Updated outline")
    saved = send(http, post, "save", **draft_request(state)).json()
    assert saved["draft"] is None
    assert len(saved["versions"]) == 2
    assert saved["shown"]["number"] == 2
    old = http.get(url(post), {"version": base["id"]}).json()
    assert old["shown"]["content"] == base["content"]
    assert old["latest"] == saved["latest"]
    assert send(http, post, "edit", base=base["id"]).status_code == 409
    state = send(http, post, "edit", base=saved["latest"]).json()
    state = send(http, post, "autosave", **draft_request(state, "Discard this")).json()
    discarded = send(http, post, "discard", **draft_request(state)).json()
    assert discarded["shown"]["content"] == saved["shown"]["content"]
    assert len(discarded["versions"]) == 2
    assert discarded["draft"] is None


def test_compare_defaults_to_last_two_versions_and_accepts_two_selected_versions(http, post):
    first = send(http, post, "edit", base=None).json()
    saved = send(http, post, "save", **draft_request(first, "First version")).json()
    first_id = saved["latest"]
    second = send(http, post, "edit", base=first_id).json()
    saved = send(http, post, "save", **draft_request(second, "Second version")).json()
    second_id = saved["latest"]
    third = send(http, post, "edit", base=second_id).json()
    saved = send(http, post, "save", **draft_request(third, "Third version")).json()

    default = http.get(url(post)).json()
    assert default["comparison"]["old"]["id"] == second_id
    assert default["comparison"]["new"]["id"] == saved["latest"]
    assert default["comparison"]["old"]["author"]
    assert default["comparison"]["old"]["saved_at"]

    selected = http.get(url(post), {"compare_old": first_id, "compare_new": saved["latest"]}).json()
    assert selected["comparison"]["old"]["content"] == content("First version")
    assert selected["comparison"]["new"]["content"] == content("Third version")
    same = http.get(url(post), {"compare_old": second_id, "compare_new": second_id}).json()
    assert same["comparison"]["old"]["content"] == same["comparison"]["new"]["content"]
    assert http.get(url(post), {"compare_old": first_id}).status_code == 400


def test_noop_save_does_not_add_version(http, post):
    state = send(http, post, "edit", base=None).json()
    data = draft_request(state)
    data["content"] = state["draft"]["content"]
    result = send(http, post, "save", **data).json()
    assert result["draft"] is None
    assert len(result["versions"]) == 1


def test_comparison_is_read_only_and_rejects_drafts_and_other_posts(http, post, workspace):
    state = send(http, post, "edit", base=None).json()
    first_id = state["latest"]
    saved = send(http, post, "save", **draft_request(state)).json()
    draft = send(http, post, "edit", base=saved["latest"]).json()
    draft = send(http, post, "autosave", **draft_request(draft, "PRIVATE DRAFT")).json()
    reader = User.objects.create_user(
        email="compare-reader@example.invalid", name="Reader", tos_accepted_at=timezone.now()
    )
    WorkspaceMembership.objects.create(workspace=workspace, user=reader, workspace_role="client")
    http.force_login(reader)
    count = TextVersion.objects.count()
    query = {"compare_old": first_id, "compare_new": saved["latest"]}
    response = http.get(url(post), query)
    assert response.status_code == 200
    assert response.json()["comparison"]["new"]["content"] == content("Updated outline")
    assert "PRIVATE DRAFT" not in response.content.decode()
    assert TextVersion.objects.count() == count
    query["compare_new"] = draft["draft"]["id"]
    assert http.get(url(post), query).status_code == 404
    other = Post.objects.create(workspace=workspace, author=post.author, title="Other script")
    assert http.get(url(other), {"compare_old": first_id, "compare_new": first_id}).status_code == 404
    WorkspaceMembership.objects.filter(workspace=workspace, user=reader).delete()
    assert http.get(url(post), query).status_code == 403


def test_comparison_size_limit_keeps_version_metadata_and_saved_text_readable(http, post):
    state = send(http, post, "edit", base=None).json()
    first_id = state["latest"]
    saved = send(http, post, "save", **draft_request(state, "x" * 260_000)).json()
    response = http.get(url(post), {"compare_old": first_id, "compare_new": saved["latest"]})
    assert response.status_code == 200
    result = response.json()
    assert "too large" in result["comparison_error"]
    assert result["comparison"]["old"]["id"] == first_id
    assert result["comparison"]["new"]["saved_at"]
    assert "content" not in result["comparison"]["new"]
    assert result["shown"]["content"] == content("x" * 260_000)


@pytest.mark.parametrize("action", ["save", "autosave", "discard"])
def test_two_tabs_cannot_overwrite_newer_revision(http, post, action):
    state = send(http, post, "edit", base=None).json()
    stale = draft_request(state, "Stale tab")
    send(http, post, "autosave", **draft_request(state, "Newer tab"))
    response = send(http, post, action, **stale)
    assert response.status_code == 409
    assert "another tab" in response.json()["error"]
    assert TextVersion.objects.get(pk=state["draft"]["id"]).text == "Newer tab"
    assert send(http, post, "edit", base=state["latest"]).status_code == 409


@pytest.mark.parametrize("role", ["client", "viewer", "editor"])
def test_other_reader_never_receives_private_draft(http, post, workspace, role):
    state = send(http, post, "edit", base=None).json()
    send(http, post, "autosave", **draft_request(state, "PRIVATE DRAFT"))
    other = User.objects.create_user(
        email=f"{role}@example.invalid", name="Other editor", tos_accepted_at=timezone.now()
    )
    WorkspaceMembership.objects.create(workspace=workspace, user=other, workspace_role=role)
    http.force_login(other)
    response = http.get(url(post))
    assert response.status_code == 200
    assert "PRIVATE DRAFT" not in response.content.decode()
    assert "content" not in response.json()["draft"]
    assert response.json()["draft"]["owner"]
    assert http.get(url(post), {"version": state["draft"]["id"]}).status_code == 404
    assert send(http, post, "edit", base=state["latest"]).status_code == (409 if role == "editor" else 403)
    for action in ("autosave", "save", "discard"):
        assert send(http, post, action, **draft_request(state)).status_code == 403


@pytest.mark.parametrize("action", ["edit", "autosave", "save", "discard"])
def test_revoked_write_permission_is_checked_for_every_request(http, post, workspace, user, action):
    state = send(http, post, "edit", base=None).json()
    WorkspaceMembership.objects.filter(workspace=workspace, user=user).update(workspace_role="client")
    data = {"base": state["latest"]} if action == "edit" else draft_request(state)
    assert send(http, post, action, **data).status_code == 403
    assert http.get(url(post)).json()["can_edit"] is False


def test_nonmember_archived_workspace_anonymous_and_cross_post(http, post, workspace, user, organization):
    state = send(http, post, "edit", base=None).json()
    other = Post.objects.create(workspace=workspace, author=user, title="Another post")
    assert send(http, other, "save", **draft_request(state)).status_code == 409
    assert http.get(url(other), {"version": state["latest"]}).status_code == 404
    other_workspace = Workspace.objects.create(organization=organization, name="Other client")
    assert http.get(url(post).replace(str(workspace.pk), str(other_workspace.pk))).status_code == 403
    WorkspaceMembership.objects.create(workspace=other_workspace, user=user, workspace_role="owner")
    assert http.get(url(post).replace(str(workspace.pk), str(other_workspace.pk))).status_code == 404
    WorkspaceMembership.objects.filter(workspace=workspace, user=user).delete()
    assert http.get(url(post)).status_code == 403
    assert send(http, post, "discard", **draft_request(state)).status_code == 403
    WorkspaceMembership.objects.create(workspace=workspace, user=user, workspace_role="owner")
    workspace.is_archived = True
    workspace.save(update_fields=["is_archived"])
    assert http.get(url(post)).status_code == 403
    http.logout()
    assert http.get(url(post)).status_code == 302
    assert send(http, post, "edit", base=None).status_code == 302


@pytest.mark.parametrize(
    "body", ["{broken", "[]", "null", '{"action": []}', '{"action": "bad"}', "[" * 2000 + "]" * 2000]
)
def test_invalid_requests_are_rejected(http, post, body):
    assert http.post(url(post), body, content_type="application/json").status_code == 400
    assert not TextDocument.objects.filter(post=post).exists()


@pytest.mark.parametrize("revision", [None, -1, True, "0", 0.5])
def test_invalid_revisions_are_rejected(http, post, revision):
    state = send(http, post, "edit", base=None).json()
    data = draft_request(state)
    data["revision"] = revision
    assert send(http, post, "save", **data).status_code == 400


def test_invalid_schema_and_oversize_keep_existing_draft(http, post):
    state = send(http, post, "edit", base=None).json()
    data = draft_request(state)
    data["content"] = {"type": "doc", "content": [{"type": "unknownNode"}]}
    assert send(http, post, "save", **data).status_code == 400
    data["content"] = content("x" * 1_010_000)
    assert send(http, post, "autosave", **data).status_code == 400
    draft = TextVersion.objects.get(pk=state["draft"]["id"])
    assert draft.revision == 0 and draft.state == "draft"
    assert http.get(url(post), {"version": "invalid"}).status_code == 400
    assert http.delete(url(post)).status_code == 405


def test_csrf_is_required(post, user):
    secure = Client(enforce_csrf_checks=True)
    secure.force_login(user)
    assert send(secure, post, "edit", base=None).status_code == 403
    assert not TextDocument.objects.filter(post=post).exists()


def test_contributor_can_only_edit_their_own_post(http, post, workspace):
    contributor = User.objects.create_user(
        email="writer@example.invalid", name="Writer", tos_accepted_at=timezone.now()
    )
    WorkspaceMembership.objects.create(workspace=workspace, user=contributor, workspace_role="contributor")
    http.force_login(contributor)
    assert http.get(url(post)).json()["can_edit"] is False
    assert send(http, post, "edit", base=None).status_code == 403
    own = Post.objects.create(workspace=workspace, author=contributor, title="Own script")
    assert http.get(url(own)).json()["can_edit"] is True
    assert send(http, own, "edit", base=None).status_code == 200


def test_orphan_recovery_is_offered_only_to_authorized_admin(http, post, workspace, user):
    state = send(http, post, "edit", base=None).json()
    editor = User.objects.create_user(email="editor@example.invalid", name="Editor", tos_accepted_at=timezone.now())
    WorkspaceMembership.objects.create(workspace=workspace, user=editor, workspace_role="editor")
    admin = User.objects.create_user(email="manager@example.invalid", name="Manager", tos_accepted_at=timezone.now())
    WorkspaceMembership.objects.create(workspace=workspace, user=admin, workspace_role="manager")
    http.force_login(admin)
    assert http.get(url(post)).json()["draft"]["recoverable"] is False
    WorkspaceMembership.objects.filter(workspace=workspace, user=user).delete()
    http.force_login(editor)
    assert http.get(url(post)).json()["draft"]["recoverable"] is False
    http.force_login(admin)
    assert http.get(url(post)).json()["draft"]["recoverable"] is True
    result = send(http, post, "edit", base=state["latest"])
    assert result.status_code == 200
    assert result.json()["draft"]["own"]
    assert result.json()["draft"]["id"] != state["draft"]["id"]


@pytest.mark.django_db(transaction=True)
def test_simultaneous_first_edit_creates_one_document_and_one_draft(post, user):
    barrier = Barrier(2)

    def edit():
        close_old_connections()
        try:
            http = Client()
            http.force_login(user)
            barrier.wait(timeout=10)
            return send(http, post, "edit", base=None).status_code
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: edit(), range(2)))
    assert sorted(results) == [200, 409]
    assert TextDocument.objects.filter(post=post).count() == 1
    assert TextVersion.objects.filter(document__post=post, state="draft").count() == 1
