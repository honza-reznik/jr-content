# JR Content: přidáno 2026-10-24 (blok F9-B01), AGPL-3.0.
"""REST API for scripts, captions, stages and comments of a content (F9-B01)."""

import json
import pathlib
from concurrent.futures import ThreadPoolExecutor
from queue import Queue
from threading import Event
from time import monotonic

import pytest
from django.core.cache import cache
from django.db import connection, connections
from django.test import Client, override_settings

from apps.accounts.models import User
from apps.api.routers import content as content_routes
from apps.api_keys.services import issue_api_key
from apps.approvals.models import PostComment
from apps.composer.models import PlatformPost, Post
from apps.jrc_channels.services import create_manual_channel
from apps.jrc_production.services import create_content
from apps.jrc_texts import services as texts
from apps.jrc_texts.models import TextDocument, TextVersion
from apps.members.models import OrgMembership, WorkspaceMembership
from apps.workspaces.models import Workspace

pytestmark = pytest.mark.django_db
ROLES = ["owner", "manager", "editor", "contributor", "client", "viewer"]


@pytest.fixture(autouse=True)
def _jrc_mode():
    cache.delete("agent_api:auth_fail:127.0.0.1")
    with override_settings(JRC_SOCIAL_PROVIDERS_ENABLED=False):
        yield


@pytest.fixture
def workspace(organization, user):
    workspace = Workspace.objects.create(organization=organization, name="Content API")
    WorkspaceMembership.objects.create(workspace=workspace, user=user, workspace_role="owner")
    return workspace


@pytest.fixture
def channel(workspace):
    return create_manual_channel(workspace, "instagram", "IG")


@pytest.fixture
def content(workspace, user, channel):
    return create_content(
        workspace,
        "API content",
        user,
        social_account_ids=workspace.social_accounts.filter(jrc_channel__is_enabled=True).values_list("pk", flat=True),
    )


def make_key(workspace, user, accounts=None, permissions=None):
    """Issue a key with all permissions the issuer holds now (or the given subset)."""
    if permissions is None:
        membership = WorkspaceMembership.objects.get(workspace=workspace, user=user)
        permissions = [k for k, v in membership.effective_permissions.items() if v]
    if accounts is None:
        accounts = [pp.social_account for pp in PlatformPost.objects.filter(post__workspace=workspace)]
        accounts = accounts or list(workspace.social_accounts.all())
    return issue_api_key(
        workspace=workspace, social_accounts=accounts, issued_by=user, name="t", permissions=permissions
    )


def api(key, method, path, data=None):
    client = Client(HTTP_AUTHORIZATION=f"Bearer {key.plaintext_token}")
    kwargs = {"secure": True}
    if data is not None:
        kwargs.update(data=json.dumps(data), content_type="application/json")
    return getattr(client, method)(f"/api/v1/posts/{path}", **kwargs)


@pytest.fixture
def key(workspace, user, org_owner, content):
    return make_key(workspace, user)


# ---------------------------------------------------------------------------
# AK1 script
# ---------------------------------------------------------------------------


def test_script_versions_and_conflicts(key, content):
    empty = api(key, "get", f"{content.pk}/script")
    assert empty.status_code == 200
    assert empty.json()["version"] is None and empty.json()["versions"] == []

    first = api(key, "post", f"{content.pk}/script", {"markdown": "# Hook\n\nFirst take"})
    assert first.status_code == 201, first.content
    v1 = first.json()["version"]
    assert v1["number"] == 1 and v1["source"] == "api"
    assert "First take" in first.json()["markdown"]

    # The script has a version, so the base is mandatory and must be the newest one.
    assert api(key, "post", f"{content.pk}/script", {"markdown": "No base"}).status_code == 409
    second = api(key, "post", f"{content.pk}/script", {"markdown": "Second take", "base_version": v1["id"]})
    assert second.status_code == 201
    v2 = second.json()["version"]
    assert v2["number"] == 2
    stale = api(key, "post", f"{content.pk}/script", {"markdown": "Stale", "base_version": v1["id"]})
    assert stale.status_code == 409
    same = api(key, "post", f"{content.pk}/script", {"markdown": "Second take", "base_version": v2["id"]})
    assert same.status_code == 200 and same.json()["version"]["id"] == v2["id"]

    listing = api(key, "get", f"{content.pk}/script").json()
    assert [v["number"] for v in listing["versions"]] == [2, 1]
    assert listing["markdown"].strip() == "Second take"
    # The old version is still there, unchanged.
    old = api(key, "get", f"{content.pk}/script?version={v1['id']}").json()
    assert "First take" in old["markdown"]
    other = api(key, "get", f"{content.pk}/script?version=00000000-0000-0000-0000-000000000000")
    assert other.status_code == 404


def test_script_first_version_with_base_and_unknown_base_conflict(key, content):
    fake = "00000000-0000-0000-0000-000000000001"
    assert api(key, "post", f"{content.pk}/script", {"markdown": "x", "base_version": fake}).status_code == 409
    created = api(key, "post", f"{content.pk}/script", {"markdown": "x"})
    assert created.status_code == 201
    assert api(key, "post", f"{content.pk}/script", {"markdown": "y", "base_version": fake}).status_code == 409


def test_script_validation_and_editor_draft(key, content, user):
    first = api(key, "post", f"{content.pk}/script", {"markdown": "One"}).json()["version"]
    # Over the size limit: refused before any service runs.
    too_big = api(key, "post", f"{content.pk}/script", {"markdown": "x" * 1_000_001, "base_version": first["id"]})
    assert too_big.status_code == 422
    assert api(key, "post", f"{content.pk}/script", {"base_version": first["id"]}).status_code == 422
    # Somebody working in the editor blocks a parallel write instead of losing the draft.
    texts.edit(first["id"], user)
    blocked = api(key, "post", f"{content.pk}/script", {"markdown": "Two", "base_version": first["id"]})
    assert blocked.status_code == 409
    assert TextVersion.objects.filter(document__post=content, state="draft").count() == 1
    # A draft never appears in the API.
    assert [v["number"] for v in api(key, "get", f"{content.pk}/script").json()["versions"]] == [1]


# ---------------------------------------------------------------------------
# AK2 captions
# ---------------------------------------------------------------------------


def variant_of(content):
    return content.platform_posts.get()


def test_caption_versions_copy_and_limit(key, content):
    variant = variant_of(content)
    path = f"{content.pk}/captions/{variant.pk}"
    listing = api(key, "get", f"{content.pk}/captions").json()
    assert [c["platform_post_id"] for c in listing] == [str(variant.pk)]
    assert listing[0]["char_limit"] == 2200 and listing[0]["version"] is None

    created = api(key, "post", path, {"text": "Hello 🌱"})
    assert created.status_code == 201, created.content
    body = created.json()
    assert body["text"] == "Hello 🌱" and body["char_count"] == 7
    # The history starts from the caption the copy had, then the new text.
    assert [v["number"] for v in body["versions"]] == [2, 1]
    variant.refresh_from_db()
    assert variant.platform_specific_caption == "Hello 🌱"

    # Stale and missing bases conflict; a patch with the right base is a new version.
    assert api(key, "post", path, {"text": "No base"}).status_code == 409
    v2 = body["version"]["id"]
    patched = api(key, "patch", path, {"text": "Second", "base_version": v2})
    assert patched.status_code == 201 and patched.json()["version"]["number"] == 3
    assert api(key, "patch", path, {"text": "Stale", "base_version": v2}).status_code == 409
    same = api(key, "post", path, {"text": "Second", "base_version": patched.json()["version"]["id"]})
    assert same.status_code == 200
    variant.refresh_from_db()
    assert variant.platform_specific_caption == "Second"
    assert TextVersion.objects.filter(document__platform_post=variant, state="final").count() == 3

    # Over the Instagram limit: nothing changes.
    long_text = "x" * 2201
    over = api(key, "post", path, {"text": long_text, "base_version": patched.json()["version"]["id"]})
    assert over.status_code == 422 and "2200" in over.json()["detail"]
    variant.refresh_from_db()
    assert variant.platform_specific_caption == "Second"
    exact = api(key, "post", path, {"text": "y" * 2200, "base_version": patched.json()["version"]["id"]})
    assert exact.status_code == 201
    assert api(key, "get", path).json()["char_count"] == 2200


def test_caption_channel_scope(workspace, user, org_owner, content, channel):
    other_channel = create_manual_channel(workspace, "tiktok", "TT")
    other_variant = PlatformPost.objects.create(post=content, social_account=other_channel)
    # A key that covers every copy reads and writes; one without the second account sees neither.
    wide = make_key(workspace, user, accounts=[channel, other_channel])
    narrow = make_key(workspace, user, accounts=[channel])
    assert api(wide, "get", f"{content.pk}/captions/{other_variant.pk}").status_code == 200
    assert api(narrow, "get", f"{content.pk}/captions/{other_variant.pk}").status_code == 404
    assert api(narrow, "post", f"{content.pk}/captions/{other_variant.pk}", {"text": "x"}).status_code == 404
    # Stage moves touch every copy, so they need the whole allowlist as well.
    assert api(narrow, "get", f"{content.pk}/stage").status_code == 404
    # A switched-off channel is hidden like on the Caption tab.
    other_channel.jrc_channel.is_enabled = False
    other_channel.jrc_channel.save()
    assert api(wide, "get", f"{content.pk}/captions/{other_variant.pk}").status_code == 404
    assert len(api(wide, "get", f"{content.pk}/captions").json()) == 1


def test_caption_of_another_content_is_404(key, workspace, user, content):
    other = create_content(
        workspace,
        "Second",
        user,
        social_account_ids=workspace.social_accounts.filter(jrc_channel__is_enabled=True).values_list("pk", flat=True),
    )
    foreign_variant = other.platform_posts.get()
    assert api(key, "get", f"{content.pk}/captions/{foreign_variant.pk}").status_code == 404


def test_no_other_api_path_rewrites_or_deletes_a_versioned_caption(key, content):
    """AK2: history survives every other route of the API and MCP."""
    variant = variant_of(content)
    saved = api(key, "post", f"{content.pk}/captions/{variant.pk}", {"text": "Versioned"}).json()
    versions_before = list(
        TextVersion.objects.filter(document__platform_post=variant).values_list("pk", "text", "number")
    )

    patch = api(key, "patch", f"{content.pk}", {"caption": "Upstream overwrite attempt"})
    assert patch.status_code == 422 and "/captions/{channel_copy_id}" in patch.json()["detail"]
    variant.refresh_from_db()
    assert variant.platform_specific_caption == "Versioned"
    assert variant.effective_caption == "Versioned"
    assert api(key, "get", f"{content.pk}/captions/{variant.pk}").json()["text"] == saved["text"]
    assert list(TextVersion.objects.filter(document__platform_post=variant).values_list("pk", "text", "number")) == (
        versions_before
    )

    # A whole post has no delete route; a versioned copy cannot be removed (P1-B06).
    spec = Client(HTTP_AUTHORIZATION=f"Bearer {key.plaintext_token}").get("/api/v1/openapi.json", secure=True)
    paths = spec.json()["paths"]
    assert {p for p, ops in paths.items() if "delete" in ops and "/posts" in p} == {
        "/api/v1/posts/{post_id}/channels/{platform_post_id}"
    }
    assert api(key, "delete", f"{content.pk}/channels/{variant.pk}").status_code == 422
    assert api(key, "delete", f"{content.pk}").status_code in (404, 405)
    assert PlatformPost.objects.filter(pk=variant.pk).exists()

    # Static guard: only the text service writes a channel caption after creation.
    root = pathlib.Path(__file__).resolve().parents[2]
    offenders = [
        str(path.relative_to(root))
        for folder in ("api", "mcp")
        for path in (root / folder).rglob("*.py")
        if "tests" not in path.parts and "platform_specific_caption" in path.read_text()
    ]
    assert offenders == []


# ---------------------------------------------------------------------------
# AK3 stage
# ---------------------------------------------------------------------------


def test_stage_read_and_move(key, content):
    stage = api(key, "get", f"{content.pk}/stage").json()
    assert stage["stage"] == "idea" and "script" in stage["available_moves"]
    moved = api(key, "post", f"{content.pk}/stage", {"stage": "script", "expected_stage": "idea"})
    assert moved.status_code == 200 and moved.json()["stage"] == "script"
    assert api(key, "post", f"{content.pk}/stage", {"stage": "shooting", "expected_stage": "idea"}).status_code == 409
    assert api(key, "post", f"{content.pk}/stage", {"stage": "nonsense", "expected_stage": "script"}).status_code == 422
    # Approval needs a move the role may make from this stage.
    assert api(key, "post", f"{content.pk}/stage", {"stage": "approved", "expected_stage": "script"}).status_code == 403
    # Ready needs a date; the board's own rule says 403 first because the stage is not reachable.
    review = api(key, "post", f"{content.pk}/stage", {"stage": "review", "expected_stage": "script"})
    assert review.status_code == 200 and review.json()["stage"] == "review"
    assert api(key, "get", f"{content.pk}/stage").json()["stage"] == "review"


# ---------------------------------------------------------------------------
# AK4 comments
# ---------------------------------------------------------------------------


def test_comments_internal_external_and_replies(workspace, user, org_owner, content):
    team = make_key(workspace, user)
    client_user = User.objects.create_user(email="client-api@example.test")
    WorkspaceMembership.objects.create(workspace=workspace, user=client_user, workspace_role="client")
    OrgMembership.objects.create(user=client_user, organization=workspace.organization, org_role="owner")
    client_key = make_key(workspace, client_user)

    internal = api(team, "post", f"{content.pk}/comments", {"body": "Team only"})
    assert internal.status_code == 201 and internal.json()["visibility"] == "internal"
    shared = api(team, "post", f"{content.pk}/comments", {"body": "For client", "visibility": "external"})
    assert shared.json()["visibility"] == "external"
    secret_reply = api(
        team, "post", f"{content.pk}/comments", {"body": "SECRET REPLY", "parent_id": internal.json()["id"]}
    )
    assert secret_reply.json()["visibility"] == "internal"
    open_reply = api(team, "post", f"{content.pk}/comments", {"body": "Open reply", "parent_id": shared.json()["id"]})
    assert open_reply.status_code == 201

    team_view = api(team, "get", f"{content.pk}/comments").json()
    assert {c["body"] for c in team_view} == {"Team only", "For client"}
    client_view = api(client_key, "get", f"{content.pk}/comments")
    text = client_view.content.decode()
    assert "Team only" not in text and "SECRET REPLY" not in text
    assert [c["body"] for c in client_view.json()] == ["For client"]
    assert [r["body"] for r in client_view.json()[0]["replies"]] == ["Open reply"]

    # A client cannot write internal comments nor reply into an internal thread.
    mine = api(client_key, "post", f"{content.pk}/comments", {"body": "From client", "visibility": "internal"})
    assert mine.status_code == 201 and mine.json()["visibility"] == "external"
    hidden_parent = api(
        client_key, "post", f"{content.pk}/comments", {"body": "Probe", "parent_id": internal.json()["id"]}
    )
    assert hidden_parent.status_code == 404
    assert not PostComment.objects.filter(body="Probe").exists()
    assert api(team, "post", f"{content.pk}/comments", {"body": "   "}).status_code == 422
    assert api(team, "post", f"{content.pk}/comments", {"body": "x", "visibility": "secret"}).status_code == 422


# ---------------------------------------------------------------------------
# AK5 permissions
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("role", ROLES)
@pytest.mark.parametrize("own", [True, False])
def test_role_matrix_matches_the_ui(workspace, user, org_owner, content, role, own):
    if not own:
        Post.objects.filter(pk=content.pk).update(author=User.objects.create_user(email="author@example.test"))
    WorkspaceMembership.objects.filter(workspace=workspace, user=user).update(workspace_role=role)
    key = make_key(workspace, user)
    can_write_text = role in ("owner", "manager", "editor") or (role == "contributor" and own)
    can_move = can_write_text or (role == "client" and own)
    variant = variant_of(content)

    script = api(key, "get", f"{content.pk}/script")
    assert script.status_code == 200 and script.json()["can_edit"] == can_write_text
    assert api(key, "get", f"{content.pk}/captions/{variant.pk}").status_code == 200
    assert api(key, "get", f"{content.pk}/comments").status_code == 200
    stage = api(key, "get", f"{content.pk}/stage").json()
    assert bool(stage["available_moves"]) == can_move

    write_script = api(key, "post", f"{content.pk}/script", {"markdown": "Role text"})
    assert write_script.status_code == (201 if can_write_text else 403)
    write_caption = api(key, "post", f"{content.pk}/captions/{variant.pk}", {"text": "Role caption"})
    assert write_caption.status_code == (201 if can_write_text else 403)
    move = api(key, "post", f"{content.pk}/stage", {"stage": "script", "expected_stage": "idea"})
    assert move.status_code == (200 if can_move else 403)
    comment = api(key, "post", f"{content.pk}/comments", {"body": "Role comment"})
    # Viewer keys hold neither create_posts nor approve_posts, so they are read-only.
    assert comment.status_code == (403 if role == "viewer" else 201)
    if role != "viewer":
        assert comment.json()["visibility"] == ("external" if role == "client" else "internal")
    if not can_write_text:
        assert not TextDocument.objects.filter(post=content).exists()
        variant.refresh_from_db()
        assert variant.platform_specific_caption is None


def test_key_permissions_narrow_the_issuer(workspace, user, org_owner, content):
    variant = variant_of(content)
    read_only = make_key(workspace, user, permissions=["view_analytics"])
    assert api(read_only, "get", f"{content.pk}/script").status_code == 200
    assert api(read_only, "post", f"{content.pk}/script", {"markdown": "x"}).status_code == 403
    assert api(read_only, "post", f"{content.pk}/captions/{variant.pk}", {"text": "x"}).status_code == 403
    assert api(read_only, "get", f"{content.pk}/comments").status_code == 200
    assert api(read_only, "post", f"{content.pk}/comments", {"body": "Analytics key"}).status_code == 403
    assert not PostComment.objects.filter(body="Analytics key").exists()
    assert (
        api(read_only, "post", f"{content.pk}/stage", {"stage": "script", "expected_stage": "idea"}).status_code == 403
    )

    writer = make_key(workspace, user, permissions=["create_posts"])
    assert api(writer, "post", f"{content.pk}/comments", {"body": "Writer key"}).status_code == 201
    assert api(writer, "post", f"{content.pk}/script", {"markdown": "x"}).status_code == 201
    # The owner could approve in the UI; this key was not granted approve_posts.
    assert api(writer, "post", f"{content.pk}/stage", {"stage": "review", "expected_stage": "idea"}).status_code == 200
    stage = api(writer, "get", f"{content.pk}/stage").json()
    assert "approved" not in stage["available_moves"]
    assert (
        api(writer, "post", f"{content.pk}/stage", {"stage": "approved", "expected_stage": "review"}).status_code == 403
    )


def test_demoted_issuer_shrinks_the_key(workspace, user, org_owner, content):
    key = make_key(workspace, user)
    WorkspaceMembership.objects.filter(workspace=workspace, user=user).update(workspace_role="viewer")
    assert api(key, "post", f"{content.pk}/script", {"markdown": "x"}).status_code == 403
    # The key's grants shrink with the issuer's current role, comments included.
    assert api(key, "post", f"{content.pk}/comments", {"body": "Demoted"}).status_code == 403
    assert api(key, "get", f"{content.pk}/script").status_code == 200


def test_foreign_workspace_and_anonymous(workspace, user, org_owner, content, organization):
    other_ws = Workspace.objects.create(organization=organization, name="Foreign")
    WorkspaceMembership.objects.create(workspace=other_ws, user=user, workspace_role="owner")
    create_manual_channel(other_ws, "instagram", "Foreign IG")
    foreign = create_content(
        other_ws,
        "Foreign content",
        user,
        social_account_ids=other_ws.social_accounts.filter(jrc_channel__is_enabled=True).values_list("pk", flat=True),
    )
    key = make_key(workspace, user)
    fvariant = foreign.platform_posts.get()
    for method, path, data in [
        ("get", f"{foreign.pk}/script", None),
        ("post", f"{foreign.pk}/script", {"markdown": "x"}),
        ("get", f"{foreign.pk}/captions", None),
        ("get", f"{foreign.pk}/captions/{fvariant.pk}", None),
        ("post", f"{foreign.pk}/captions/{fvariant.pk}", {"text": "x"}),
        ("get", f"{foreign.pk}/stage", None),
        ("post", f"{foreign.pk}/stage", {"stage": "script", "expected_stage": "idea"}),
        ("get", f"{foreign.pk}/comments", None),
        ("post", f"{foreign.pk}/comments", {"body": "x"}),
    ]:
        assert api(key, method, path, data).status_code == 404, (method, path)
    assert not TextDocument.objects.filter(post=foreign).exists()
    assert not PostComment.objects.filter(post=foreign).exists()
    anonymous = Client().get(f"/api/v1/posts/{content.pk}/script", secure=True)
    assert anonymous.status_code == 401


def test_openapi_documents_the_content_routes(key):
    spec = Client(HTTP_AUTHORIZATION=f"Bearer {key.plaintext_token}").get("/api/v1/openapi.json", secure=True).json()
    paths = spec["paths"]
    for suffix, methods in {
        "/script": {"get", "post"},
        "/captions": {"get"},
        "/captions/{platform_post_id}": {"get", "post", "patch"},
        "/stage": {"get", "post"},
        "/comments": {"get", "post"},
    }.items():
        entry = paths[f"/api/v1/posts/{{post_id}}{suffix}"]
        assert methods <= set(entry)
        assert all(entry[m].get("summary") for m in methods)
    assert "base_version" in json.dumps(spec["components"]["schemas"]["ScriptWrite"])


def test_audit_rows_for_writes_and_failures(key, content):
    from apps.api_keys.models import ApiKeyAuditLog

    api(key, "post", f"{content.pk}/script", {"markdown": "Audited"})
    api(key, "post", f"{content.pk}/script", {"markdown": "Conflict"})
    actions = set(ApiKeyAuditLog.objects.filter(api_key=key.api_key).values_list("action", flat=True))
    assert {"script.write", "script.write.409"} <= actions


# ---------------------------------------------------------------------------
# Captions through the upstream post routes
# ---------------------------------------------------------------------------


def test_upstream_post_routes_cannot_write_unversioned_captions(key, workspace, user, channel, content):
    account_id = str(channel.pk)
    created = api(key, "post", "", {"social_account_id": account_id, "caption": "Base caption", "action": "draft"})
    assert created.status_code == 201, created.content
    new_id = created.json()["id"]

    # An override caption would create channel text without a version.
    override = api(
        key,
        "post",
        "",
        {
            "social_account_id": account_id,
            "caption": "Base",
            "action": "draft",
            "platform_overrides": [{"social_account_id": account_id, "caption": "Override"}],
        },
    )
    assert override.status_code == 422 and "/captions/" in override.json()["detail"]
    assert Post.objects.filter(workspace=workspace).count() == 2  # fixture content + the one created above
    # Title and first comment overrides are not versioned texts and stay possible.
    title_only = api(
        key,
        "post",
        "",
        {
            "social_account_id": account_id,
            "caption": "Base",
            "action": "draft",
            "platform_overrides": [{"social_account_id": account_id, "title": "Channel title"}],
        },
    )
    assert title_only.status_code == 201

    # PATCH of the caption is refused before the first version and after it.
    before = api(key, "patch", new_id, {"caption": "Changed before history"})
    assert before.status_code == 422
    assert Post.objects.get(pk=new_id).caption == "Base caption"
    variant = Post.objects.get(pk=new_id).platform_posts.get()
    assert api(key, "get", f"{new_id}/captions/{variant.pk}").json()["text"] == "Base caption"
    api(key, "post", f"{new_id}/captions/{variant.pk}", {"text": "Versioned"})
    after = api(key, "patch", new_id, {"caption": "Changed after history"})
    assert after.status_code == 422
    # Other fields of the same PATCH still work.
    assert api(key, "patch", new_id, {"title": "New title"}).status_code == 200
    variant.refresh_from_db()
    assert variant.platform_specific_caption == "Versioned"
    assert Post.objects.get(pk=new_id).caption == "Base caption"


# P1-B06: content without channels and explicitly selected channel sets.
def test_create_channel_less_content_script_comments_stage_and_idempotency(key, workspace):
    response = api(key, "post", "", {"title": "Unassigned idea", "idempotency_key": "unassigned"})
    assert response.status_code == 201, response.content
    post_id = response.json()["id"]
    assert response.json()["platform_posts"] == []
    assert api(key, "post", "", {"title": "Unassigned idea", "idempotency_key": "unassigned"}).json()["id"] == post_id
    assert api(key, "get", post_id).status_code == 200
    assert api(key, "get", f"{post_id}/captions").json() == []
    assert api(key, "post", f"{post_id}/script", {"markdown": "# A channel can wait"}).status_code == 201
    assert api(key, "post", f"{post_id}/comments", {"body": "Keep this idea"}).status_code == 201
    assert len(api(key, "get", f"{post_id}/comments").json()) == 1
    stage = api(key, "get", f"{post_id}/stage").json()
    assert set(stage["available_moves"]) == {"script", "shooting", "editing"}
    moved = api(key, "post", f"{post_id}/stage", {"stage": "editing", "expected_stage": "idea"})
    assert moved.status_code == 200 and moved.json()["stage"] == "editing"
    review = api(key, "post", f"{post_id}/stage", {"stage": "review", "expected_stage": "editing"})
    assert review.status_code == 422 and "Add a channel" in review.json()["detail"]
    schedule = api(key, "post", f"{post_id}/schedule", {"scheduled_at": "2099-01-01T12:00:00Z"})
    assert schedule.status_code == 422 and "Add a channel" in schedule.json()["detail"]
    assert (
        api(
            key, "post", "", {"title": "No schedule", "action": "schedule", "scheduled_at": "2099-01-01T12:00:00Z"}
        ).status_code
        == 422
    )
    assert not Post.objects.filter(workspace=workspace, title="No schedule").exists()


def test_multi_channel_creation_scope_and_overrides(workspace, user, channel, org_owner):
    second = create_manual_channel(workspace, "custom", "Web")
    key = make_key(workspace, user, accounts=[channel, second])
    payload = {"title": "Two explicit channels", "social_account_ids": [str(channel.pk), str(second.pk)]}
    response = api(key, "post", "", payload)
    assert response.status_code == 201, response.content
    assert {p["social_account_id"] for p in response.json()["platform_posts"]} == {str(channel.pk), str(second.pk)}
    narrow = make_key(workspace, user, accounts=[channel])
    assert api(narrow, "post", "", payload).status_code == 403
    assert api(narrow, "get", response.json()["id"]).status_code == 404
    both = {**payload, "social_account_id": str(channel.pk)}
    assert api(key, "post", "", both).status_code == 422
    assert api(key, "post", "", {"social_account_ids": [str(channel.pk), str(channel.pk)]}).status_code == 422
    assert (
        api(
            key,
            "post",
            "",
            {**payload, "platform_overrides": [{"social_account_id": str(second.pk), "caption": "Unversioned"}]},
        ).status_code
        == 422
    )
    assert Post.objects.filter(title="Two explicit channels").count() == 1


def test_explicit_api_channel_add_remove_and_allowlist(workspace, user, channel, org_owner):
    second = create_manual_channel(workspace, "custom", "Web")
    key = make_key(workspace, user, accounts=[channel, second])
    narrow = make_key(workspace, user, accounts=[channel])
    post = api(key, "post", "", {"title": "Add later"}).json()
    post_id = post["id"]
    assert api(narrow, "post", f"{post_id}/channels", {"social_account_id": str(second.pk)}).status_code == 403
    added = api(key, "post", f"{post_id}/channels", {"social_account_id": str(second.pk)})
    assert added.status_code == 200, added.content
    copy_id = added.json()["platform_posts"][0]["id"]
    assert (
        api(key, "post", f"{post_id}/channels", {"social_account_id": str(second.pk)}).json()["platform_posts"][0]["id"]
        == copy_id
    )
    assert api(narrow, "delete", f"{post_id}/channels/{copy_id}").status_code == 404
    removed = api(key, "delete", f"{post_id}/channels/{copy_id}")
    assert removed.status_code == 200 and removed.json()["platform_posts"] == []
    # Last copy removed: workspace access still reaches script and comments.
    assert api(narrow, "get", f"{post_id}/script").status_code == 200
    added = api(key, "post", f"{post_id}/channels", {"social_account_id": str(channel.pk)})
    copy_id = added.json()["platform_posts"][0]["id"]
    assert api(key, "post", f"{post_id}/captions/{copy_id}", {"text": "Keep history"}).status_code == 201
    assert api(key, "delete", f"{post_id}/channels/{copy_id}").status_code == 422


def test_channel_api_respects_key_and_issuer_permissions(key, workspace, user, channel):
    post_id = api(key, "post", "", {"title": "Limited key"}).json()["id"]
    read_only = make_key(workspace, user, accounts=[channel], permissions=[])
    assert api(read_only, "post", f"{post_id}/channels", {"social_account_id": str(channel.pk)}).status_code == 403
    # Contributor cannot attach a channel to another author's content.
    member = User.objects.create_user(email="channel-contributor@example.test")
    WorkspaceMembership.objects.create(workspace=workspace, user=member, workspace_role="contributor")
    OrgMembership.objects.create(user=member, organization=workspace.organization, org_role="owner")
    contributor_key = make_key(workspace, member, accounts=[channel])
    assert (
        api(contributor_key, "post", f"{post_id}/channels", {"social_account_id": str(channel.pk)}).status_code == 403
    )
    # A workspace-scoped unassigned content UUID cannot cross workspaces.
    other = Workspace.objects.create(organization=workspace.organization, name="Other workspace")
    WorkspaceMembership.objects.create(workspace=other, user=user, workspace_role="owner")
    foreign = create_content(other, "Foreign unassigned", user)
    assert api(key, "get", str(foreign.pk)).status_code == 404


def test_openapi_documents_unassigned_and_channel_routes(key):
    spec = Client(HTTP_AUTHORIZATION=f"Bearer {key.plaintext_token}").get("/api/v1/openapi.json", secure=True).json()
    schema = spec["components"]["schemas"]["CreatePostRequest"]
    assert "social_account_id" not in schema.get("required", [])
    assert "social_account_ids" in schema["properties"]
    assert "/api/v1/posts/{post_id}/channels" in spec["paths"]
    assert "delete" in spec["paths"]["/api/v1/posts/{post_id}/channels/{platform_post_id}"]


def _concurrent_channel_request(key, method, path, data=None, pid_queue=None):
    try:
        reported = False

        def report_connection(execute, sql, params, many, context):
            nonlocal reported
            if not reported:
                reported = True
                execute("SET lock_timeout = '8s'", None, False, context)
                if pid_queue is not None:
                    pid_queue.put(connection.connection.info.backend_pid)
            return execute(sql, params, many, context)

        with connection.execute_wrapper(report_connection):
            return api(key, method, path, data)
    finally:
        connections.close_all()


def _channel_request_waits_on_lock(pid, future):
    deadline = monotonic() + 5
    while monotonic() < deadline and not future.done():
        with connection.cursor() as cursor:
            cursor.execute("SELECT cardinality(pg_blocking_pids(%s)) > 0", [pid])
            if cursor.fetchone()[0]:
                return True
        Event().wait(0.01)
    return False


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("method", ["post", "delete"])
def test_channel_allowlist_is_rechecked_after_concurrent_wider_key(
    workspace, user, channel, org_owner, monkeypatch, method
):
    second = create_manual_channel(workspace, "custom", "Private B")
    third = create_manual_channel(workspace, "youtube", "Allowed C")
    post = create_content(workspace, "Concurrent channels", user, social_account_ids=[channel.pk])
    variant = post.platform_posts.get()
    wide = make_key(workspace, user, accounts=[channel, second, third])
    narrow = make_key(workspace, user, accounts=[channel, third])
    added, release = Event(), Event()
    original = content_routes.add_content_channel

    def pause_wide(*args, **kwargs):
        result = original(*args, **kwargs)
        if args[1] == second.pk:
            added.set()
            assert release.wait(8)
        return result

    monkeypatch.setattr(content_routes, "add_content_channel", pause_wide)
    path = f"{post.pk}/channels" if method == "post" else f"{post.pk}/channels/{variant.pk}"
    payload = {"social_account_id": str(third.pk)} if method == "post" else None
    pids = Queue()
    with ThreadPoolExecutor(max_workers=2) as pool:
        wide_request = pool.submit(
            _concurrent_channel_request, wide, "post", f"{post.pk}/channels", {"social_account_id": str(second.pk)}
        )
        try:
            assert added.wait(8), "Wide request did not add its private channel"
            narrow_request = pool.submit(_concurrent_channel_request, narrow, method, path, payload, pids)
            blocked = _channel_request_waits_on_lock(pids.get(timeout=8), narrow_request)
        finally:
            release.set()
        assert wide_request.result(timeout=10).status_code == 200
        response = narrow_request.result(timeout=10)
    assert blocked, "Narrow request must wait for the parent Post lock"
    assert response.status_code == 404, response.content
    assert str(second.pk) not in response.content.decode()
    assert set(post.platform_posts.values_list("social_account_id", flat=True)) == {channel.pk, second.pk}


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("method", ["post", "delete"])
def test_channel_response_is_materialized_before_releasing_parent_lock(
    workspace, user, channel, org_owner, monkeypatch, method
):
    second = create_manual_channel(workspace, "custom", "Private B")
    third = create_manual_channel(workspace, "youtube", "Allowed C")
    post = create_content(workspace, "Response isolation", user, social_account_ids=[channel.pk])
    variant = post.platform_posts.get()
    wide = make_key(workspace, user, accounts=[channel, second, third])
    narrow = make_key(workspace, user, accounts=[channel, third])
    serializing, release = Event(), Event()
    original = content_routes._post_to_response

    def pause_response(request, content):
        if request.api_key.pk == narrow.api_key.pk:
            serializing.set()
            assert release.wait(8)
        return original(request, content)

    monkeypatch.setattr(content_routes, "_post_to_response", pause_response)
    path = f"{post.pk}/channels" if method == "post" else f"{post.pk}/channels/{variant.pk}"
    payload = {"social_account_id": str(third.pk)} if method == "post" else None
    pids = Queue()
    with ThreadPoolExecutor(max_workers=2) as pool:
        narrow_request = pool.submit(_concurrent_channel_request, narrow, method, path, payload)
        try:
            assert serializing.wait(8), "Narrow request did not reach response serialization"
            wide_request = pool.submit(
                _concurrent_channel_request,
                wide,
                "post",
                f"{post.pk}/channels",
                {"social_account_id": str(second.pk)},
                pids,
            )
            blocked = _channel_request_waits_on_lock(pids.get(timeout=8), wide_request)
        finally:
            release.set()
        response = narrow_request.result(timeout=10)
        assert wide_request.result(timeout=10).status_code == 200
    assert blocked, "Wider request must wait until the narrow response has been assembled"
    assert response.status_code == 200, response.content
    assert str(second.pk) not in response.content.decode()
