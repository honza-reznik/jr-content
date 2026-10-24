# JR Content: přidáno 2026-10-24 (blok F9-B01), AGPL-3.0.
"""REST API for scripts, captions, stages and comments of a content (F9-B01)."""

import json
import pathlib

import pytest
from django.core.cache import cache
from django.test import Client, override_settings

from apps.accounts.models import User
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
    return create_content(workspace, "API content", user)


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
    other = create_content(workspace, "Second", user)
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
    assert patch.status_code == 200
    variant.refresh_from_db()
    assert variant.platform_specific_caption == "Versioned"
    assert variant.effective_caption == "Versioned"
    assert api(key, "get", f"{content.pk}/captions/{variant.pk}").json()["text"] == saved["text"]
    assert list(TextVersion.objects.filter(document__platform_post=variant).values_list("pk", "text", "number")) == (
        versions_before
    )

    # The API has no way to delete a post or channel copy.
    spec = Client(HTTP_AUTHORIZATION=f"Bearer {key.plaintext_token}").get("/api/v1/openapi.json", secure=True)
    paths = spec.json()["paths"]
    assert not [p for p, ops in paths.items() if "delete" in ops and "/posts" in p]
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
    assert comment.status_code == 201
    assert comment.json()["visibility"] == ("external" if role in ("client", "viewer") else "internal")
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
    assert (
        api(read_only, "post", f"{content.pk}/stage", {"stage": "script", "expected_stage": "idea"}).status_code == 403
    )

    writer = make_key(workspace, user, permissions=["create_posts"])
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
    assert api(key, "get", f"{content.pk}/script").status_code == 200


def test_foreign_workspace_and_anonymous(workspace, user, org_owner, content, organization):
    other_ws = Workspace.objects.create(organization=organization, name="Foreign")
    WorkspaceMembership.objects.create(workspace=other_ws, user=user, workspace_role="owner")
    create_manual_channel(other_ws, "instagram", "Foreign IG")
    foreign = create_content(other_ws, "Foreign content", user)
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
