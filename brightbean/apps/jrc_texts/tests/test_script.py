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


_devices = iter(range(10_000))


def autosave(http, post, text, *, base="latest", revision=None, device=None):
    """P2-B08: one automatic save; a fresh device (by default) always makes a new version."""
    if base == "latest":
        state = http.get(url(post)).json()
        base = state["latest"]
        revision = next((v["revision"] for v in state["versions"] if v["id"] == base), 0)
    device = device or f"test-device-{next(_devices):05d}"
    return send(http, post, "autosave", base=base, revision=revision or 0, device=device, content=content(text))


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
    # P2-B08: no Edit or Save button; the text is edited in place and saves automatically.
    assert b"Edit text" not in detail.content and b'id="script-status"' in detail.content
    for color in (b"#b91c1c", b"#c2410c", b"#a16207", b"#15803d", b"#0f766e", b"#0369a1", b"#4338ca", b"#7e22ce"):
        assert color in detail.content


def test_autosave_history_and_reading_an_older_version(http, post):
    saved = autosave(http, post, "Updated outline").json()
    assert saved["saved"]["created"] and len(saved["versions"]) == 1
    assert saved["shown"]["number"] == 1 and "draft" not in saved
    second = autosave(http, post, "Second version").json()
    assert second["shown"]["number"] == 2
    old = http.get(url(post), {"version": saved["latest"]}).json()
    assert old["shown"]["content"] == saved["shown"]["content"]
    assert old["latest"] == second["latest"]
    # A base that is no saved version of this text is refused.
    assert autosave(http, post, "x", base=str(post.pk), revision=0).status_code == 409


def test_compare_defaults_to_last_two_versions_and_accepts_two_selected_versions(http, post):
    first_id = autosave(http, post, "First version").json()["latest"]
    second_id = autosave(http, post, "Second version").json()["latest"]
    saved = autosave(http, post, "Third version").json()

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


def test_noop_autosave_does_not_add_version(http, post):
    autosave(http, post, "Same text")
    result = autosave(http, post, "Same text").json()
    assert result["saved"]["changed"] is False
    assert len(result["versions"]) == 1


def test_comparison_is_read_only_and_rejects_other_posts(http, post, workspace):
    first_id = autosave(http, post, "First version").json()["latest"]
    saved = autosave(http, post, "Updated outline").json()
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
    assert TextVersion.objects.count() == count
    other = Post.objects.create(workspace=workspace, author=post.author, title="Other script")
    assert http.get(url(other), {"compare_old": first_id, "compare_new": first_id}).status_code == 404
    WorkspaceMembership.objects.filter(workspace=workspace, user=reader).delete()
    assert http.get(url(post), query).status_code == 404


def test_client_reads_color_marks_but_cannot_write(http, post, workspace):
    saved = send(
        http,
        post,
        "autosave",
        base=None,
        revision=0,
        device="color-test",
        content={
            "type": "doc",
            "content": [
                {
                    "type": "paragraph",
                    "content": [
                        {
                            "type": "text",
                            "text": "Blue",
                            "marks": [{"type": "textColor", "attrs": {"color": "#0369a1"}}],
                        }
                    ],
                }
            ],
        },
    ).json()
    client_user = User.objects.create_user(
        email="color-reader@example.invalid", name="Color reader", tos_accepted_at=timezone.now()
    )
    WorkspaceMembership.objects.create(workspace=workspace, user=client_user, workspace_role="client")
    http.force_login(client_user)
    reading = http.get(url(post))
    assert reading.status_code == 200 and reading.json()["can_edit"] is False
    assert reading.json()["shown"]["content"]["content"][0]["content"][0]["marks"][0]["attrs"]["color"] == "#0369a1"
    assert (
        send(
            http, post, "autosave", base=saved["latest"], revision=0, device="client-color", content=content("Changed")
        ).status_code
        == 403
    )


def test_comparison_size_limit_keeps_version_metadata_and_saved_text_readable(http, post):
    first_id = autosave(http, post, "First version").json()["latest"]
    saved = autosave(http, post, "x" * 260_000).json()
    response = http.get(url(post), {"compare_old": first_id, "compare_new": saved["latest"]})
    assert response.status_code == 200
    result = response.json()
    assert "too large" in result["comparison_error"]
    assert result["comparison"]["old"]["id"] == first_id
    assert result["comparison"]["new"]["saved_at"]
    assert "content" not in result["comparison"]["new"]
    assert result["shown"]["content"] == content("x" * 260_000)


@pytest.mark.parametrize("role", ["client", "viewer"])
def test_readers_cannot_autosave(http, post, workspace, role):
    autosave(http, post, "Saved text")
    other = User.objects.create_user(email=f"{role}@example.invalid", name="Reader", tos_accepted_at=timezone.now())
    WorkspaceMembership.objects.create(workspace=workspace, user=other, workspace_role=role)
    http.force_login(other)
    assert http.get(url(post)).json()["can_edit"] is False
    assert autosave(http, post, "Not allowed").status_code == 403
    assert TextVersion.objects.filter(document__post=post).count() == 1


def test_revoked_write_permission_is_checked_for_every_request(http, post, workspace, user):
    autosave(http, post, "Saved text")
    WorkspaceMembership.objects.filter(workspace=workspace, user=user).update(workspace_role="client")
    assert autosave(http, post, "Not any more").status_code == 403
    assert http.get(url(post)).json()["can_edit"] is False


def test_nonmember_archived_workspace_anonymous_and_cross_post(http, post, workspace, user, organization):
    state = autosave(http, post, "Saved text").json()
    other = Post.objects.create(workspace=workspace, author=user, title="Another post")
    assert autosave(http, other, "x", base=state["latest"], revision=0).status_code == 409
    assert http.get(url(other), {"version": state["latest"]}).status_code == 404
    other_workspace = Workspace.objects.create(organization=organization, name="Other client")
    assert http.get(url(post).replace(str(workspace.pk), str(other_workspace.pk))).status_code == 404
    WorkspaceMembership.objects.create(workspace=other_workspace, user=user, workspace_role="owner")
    assert http.get(url(post).replace(str(workspace.pk), str(other_workspace.pk))).status_code == 404
    WorkspaceMembership.objects.filter(workspace=workspace, user=user).delete()
    assert http.get(url(post)).status_code == 404
    assert autosave(http, post, "x", base=None).status_code == 404
    WorkspaceMembership.objects.create(workspace=workspace, user=user, workspace_role="owner")
    workspace.is_archived = True
    workspace.save(update_fields=["is_archived"])
    assert http.get(url(post)).status_code == 404
    http.logout()
    assert http.get(url(post)).status_code == 302
    assert autosave(http, post, "x", base=None).status_code == 302


@pytest.mark.parametrize(
    "body",
    [
        "{broken",
        "[]",
        "null",
        '{"action": []}',
        '{"action": "bad"}',
        '{"action": "edit"}',
        '{"action": "autosave", "device": "test-device-1", "content": []}',
        "[" * 2000 + "]" * 2000,
    ],
)
def test_invalid_requests_are_rejected(http, post, body):
    assert http.post(url(post), body, content_type="application/json").status_code == 400
    assert not TextDocument.objects.filter(post=post).exists()


@pytest.mark.parametrize("revision", [None, -1, True, "0", 0.5])
def test_invalid_revisions_are_rejected(http, post, revision):
    state = autosave(http, post, "Saved text").json()
    response = send(
        http, post, "autosave", base=state["latest"], revision=revision, device="test-device-1", content=content("x")
    )
    assert response.status_code == 400


def test_invalid_schema_and_oversize_keep_the_saved_text(http, post):
    state = autosave(http, post, "Saved text").json()
    data = {"base": state["latest"], "revision": 0, "device": "test-device-2"}
    assert (
        send(http, post, "autosave", **data, content={"type": "doc", "content": [{"type": "unknownNode"}]}).status_code
        == 400
    )
    assert send(http, post, "autosave", **data, content=content("x" * 1_010_000)).status_code == 400
    assert [v.text for v in TextVersion.objects.filter(document__post=post)] == ["Saved text"]
    assert http.get(url(post), {"version": "invalid"}).status_code == 400
    assert http.delete(url(post)).status_code == 405


def test_csrf_is_required(post, user):
    secure = Client(enforce_csrf_checks=True)
    secure.force_login(user)
    assert autosave(secure, post, "x", base=None).status_code == 403
    assert not TextDocument.objects.filter(post=post).exists()


def test_contributor_can_only_edit_their_own_post(http, post, workspace):
    contributor = User.objects.create_user(
        email="writer@example.invalid", name="Writer", tos_accepted_at=timezone.now()
    )
    WorkspaceMembership.objects.create(workspace=workspace, user=contributor, workspace_role="contributor")
    http.force_login(contributor)
    assert http.get(url(post)).json()["can_edit"] is False
    assert autosave(http, post, "x", base=None).status_code == 403
    own = Post.objects.create(workspace=workspace, author=contributor, title="Own script")
    assert http.get(url(own)).json()["can_edit"] is True
    assert autosave(http, own, "Mine", base=None).status_code == 200


@pytest.mark.django_db(transaction=True)
def test_simultaneous_first_autosave_creates_one_document_and_two_versions(post, user):
    barrier = Barrier(2)

    def write(index):
        close_old_connections()
        try:
            http = Client()
            http.force_login(user)
            barrier.wait(timeout=10)
            return autosave(http, post, f"Tab {index}", base=None, device=f"test-device-race{index}").status_code
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(write, range(2)))
    assert results == [200, 200]
    assert TextDocument.objects.filter(post=post).count() == 1
    assert sorted(TextVersion.objects.filter(document__post=post).values_list("number", flat=True)) == [1, 2]
