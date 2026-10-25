from __future__ import annotations

import json

import pytest
from django.test import Client
from django.utils import timezone

from apps.api_keys import services as key_services
from apps.approvals.models import PostComment
from apps.composer.models import PlatformPost, Post
from apps.jrc_texts.models import TextDocument, TextVersion
from apps.members.models import PERMISSION_KEYS, OrgMembership, WorkspaceMembership


class _SecureClient(Client):
    def generic(self, method, path, *args, **kwargs):
        kwargs["secure"] = True
        return super().generic(method, path, *args, **kwargs)


MCP_URL = "/api/v1/mcp/"
CONTENT_TOOLS = {
    "jrc_get_script",
    "jrc_save_script",
    "jrc_get_caption",
    "jrc_save_caption",
    "jrc_list_comments",
    "jrc_add_comment",
    "jrc_get_stage",
}


def _rpc(method, params=None):
    return {"jsonrpc": "2.0", "method": method, "params": params or {}, "id": 1}


def _call(client, name, arguments):
    response = client.post(
        MCP_URL,
        data=json.dumps(_rpc("tools/call", {"name": name, "arguments": arguments})),
        content_type="application/json",
    )
    assert response.status_code == 200
    return response.json()


def _result(envelope):
    assert "error" not in envelope, envelope
    return json.loads(envelope["result"]["content"][0]["text"])


def _role_client(workspace, account, issuer, role, *, permissions=None, name=None):
    """Issue a key that reflects the issuer's current workspace role."""
    from apps.accounts.models import User

    user = User.objects.create_user(
        email=f"mcp-content-{name or role}@example.com",
        password="testpass123",
        name=f"Content {name or role}",
        tos_accepted_at=timezone.now(),
    )
    WorkspaceMembership.objects.create(user=user, workspace=workspace, workspace_role=role)
    key = key_services.issue_api_key(
        workspace=workspace,
        social_accounts=[account],
        issued_by=issuer,
        name=f"mcp-content-{name or role}",
        permissions=list(PERMISSION_KEYS) if permissions is None else permissions,
    )
    key.api_key.issued_by = user
    key.api_key.save(update_fields=["issued_by"])
    return _SecureClient(HTTP_AUTHORIZATION=f"Bearer {key.plaintext_token}"), user


@pytest.fixture
def owner(db):
    from apps.accounts.models import User
    from apps.organizations.models import Organization
    from apps.social_accounts.models import SocialAccount
    from apps.workspaces.models import Workspace

    user = User.objects.create_user(
        email="mcp-content-owner@example.com",
        password="testpass123",
        name="Content Owner",
        tos_accepted_at=timezone.now(),
    )
    organization = Organization.objects.create(name="MCP Content Org")
    workspace = Workspace.objects.create(name="MCP Content Workspace", organization=organization)
    OrgMembership.objects.create(user=user, organization=organization, org_role=OrgMembership.OrgRole.OWNER)
    membership = WorkspaceMembership.objects.create(
        user=user,
        workspace=workspace,
        workspace_role=WorkspaceMembership.WorkspaceRole.OWNER,
    )
    account = SocialAccount.objects.create(
        workspace=workspace,
        platform="linkedin_personal",
        account_platform_id="li-mcp-content",
        account_name="MCP Content LinkedIn",
        connection_status="connected",
    )
    post = Post.objects.create(workspace=workspace, author=user, title="MCP content", caption="Original caption")
    variant = PlatformPost.objects.create(post=post, social_account=account, status="draft")
    return user, workspace, membership, account, post, variant


@pytest.fixture
def owner_client(owner):
    user, workspace, _, account, _, _ = owner
    key = key_services.issue_api_key(
        workspace=workspace,
        social_accounts=[account],
        issued_by=user,
        name="mcp-content-owner",
        permissions=list(PERMISSION_KEYS),
    )
    return _SecureClient(HTTP_AUTHORIZATION=f"Bearer {key.plaintext_token}")


@pytest.mark.django_db
def test_content_tools_are_registered_and_stage_is_readable(owner_client, owner):
    _, _, _, _, post, _ = owner
    response = owner_client.post(
        MCP_URL,
        data=json.dumps(_rpc("tools/list")),
        content_type="application/json",
    )
    names = {tool["name"] for tool in response.json()["result"]["tools"]}
    assert names >= CONTENT_TOOLS

    stage = _result(_call(owner_client, "jrc_get_stage", {"post_id": str(post.pk)}))
    assert stage["post_id"] == str(post.pk)
    assert stage["stage"] == "idea"


@pytest.mark.django_db
def test_script_read_write_uses_versions_and_rejects_stale_base(owner_client, owner):
    _, _, _, _, post, _ = owner
    initial = _result(_call(owner_client, "jrc_get_script", {"post_id": str(post.pk)}))
    assert initial["version"] is None
    assert initial["markdown"] is None

    saved = _result(_call(owner_client, "jrc_save_script", {"post_id": str(post.pk), "markdown": "# First"}))
    first_id = saved["version"]["id"]
    assert saved["status"] == 201
    assert TextVersion.objects.filter(document__post=post, state=TextVersion.State.FINAL).count() == 1

    second = _result(
        _call(
            owner_client,
            "jrc_save_script",
            {"post_id": str(post.pk), "markdown": "# Second", "base_version": first_id},
        )
    )
    assert second["status"] == 201
    assert second["version"]["number"] == 2
    conflict = _call(
        owner_client,
        "jrc_save_script",
        {"post_id": str(post.pk), "markdown": "# Stale", "base_version": first_id},
    )
    assert conflict["error"]["data"]["status"] == 409
    assert TextVersion.objects.filter(document__post=post, state=TextVersion.State.FINAL).count() == 2
    assert TextVersion.objects.filter(document__post=post, state=TextVersion.State.DRAFT).count() == 0
    assert TextDocument.objects.get(post=post, kind=TextDocument.Kind.SCRIPT).versions.latest("number").source == "mcp"


@pytest.mark.django_db
def test_caption_tools_use_version_service_and_preserve_history(owner_client, owner):
    _, _, _, _, post, variant = owner
    before = _result(
        _call(
            owner_client,
            "jrc_get_caption",
            {"post_id": str(post.pk), "platform_post_id": str(variant.pk)},
        )
    )
    assert before["text"] == "Original caption"
    assert before["version"] is None

    first = _result(
        _call(
            owner_client,
            "jrc_save_caption",
            {"post_id": str(post.pk), "platform_post_id": str(variant.pk), "text": "First caption"},
        )
    )
    assert first["status"] == 201
    first_id = first["version"]["id"]
    second = _result(
        _call(
            owner_client,
            "jrc_save_caption",
            {
                "post_id": str(post.pk),
                "platform_post_id": str(variant.pk),
                "text": "Second caption",
                "base_version": first_id,
            },
        )
    )
    # The service first records the channel's pre-existing caption as version 1.
    assert second["version"]["number"] == 3
    conflict = _call(
        owner_client,
        "jrc_save_caption",
        {
            "post_id": str(post.pk),
            "platform_post_id": str(variant.pk),
            "text": "Stale caption",
            "base_version": first_id,
        },
    )
    assert conflict["error"]["data"]["status"] == 409
    variant.refresh_from_db()
    assert variant.platform_specific_caption == "Second caption"
    versions = TextVersion.objects.filter(document__platform_post=variant, state=TextVersion.State.FINAL)
    assert versions.count() == 3  # Original caption baseline plus two saved edits.
    assert not TextVersion.objects.filter(document__platform_post=variant, state=TextVersion.State.DRAFT).exists()


@pytest.mark.django_db
def test_comment_tools_enforce_write_key_and_hide_internal_comments(owner_client, owner):
    _, workspace, _, account, post, _ = owner
    internal = PostComment.objects.create(
        post=post,
        author=owner[0],
        body="Team only",
        visibility=PostComment.Visibility.INTERNAL,
    )
    external = PostComment.objects.create(
        post=post,
        author=owner[0],
        body="Client visible",
        visibility=PostComment.Visibility.EXTERNAL,
    )
    listed = _result(_call(owner_client, "jrc_list_comments", {"post_id": str(post.pk)}))
    assert {comment["id"] for comment in listed} == {str(internal.pk), str(external.pk)}
    added = _result(
        _call(
            owner_client,
            "jrc_add_comment",
            {"post_id": str(post.pk), "body": "New team note", "visibility": "internal"},
        )
    )
    assert added["body"] == "New team note"
    assert added["visibility"] == "internal"

    from apps.accounts.models import User

    client_user = User.objects.create_user(
        email="mcp-content-client@example.com",
        password="testpass123",
        name="Content Client",
        tos_accepted_at=timezone.now(),
    )
    WorkspaceMembership.objects.create(
        user=client_user,
        workspace=workspace,
        workspace_role=WorkspaceMembership.WorkspaceRole.CLIENT,
    )
    client_key = key_services.issue_api_key(
        workspace=workspace,
        social_accounts=[account],
        issued_by=owner[0],
        name="mcp-content-client",
        permissions=list(PERMISSION_KEYS),
    )
    # Model an existing key after its issuer's role has changed to client.
    client_key.api_key.issued_by = client_user
    client_key.api_key.save(update_fields=["issued_by"])
    client = _SecureClient(HTTP_AUTHORIZATION=f"Bearer {client_key.plaintext_token}")
    client_comments = _result(_call(client, "jrc_list_comments", {"post_id": str(post.pk)}))
    assert [comment["id"] for comment in client_comments] == [str(external.pk)]


@pytest.mark.django_db
def test_content_write_tools_reject_read_only_key(owner, owner_client):
    user, workspace, _, account, post, variant = owner
    read_key = key_services.issue_api_key(
        workspace=workspace,
        social_accounts=[account],
        issued_by=user,
        name="mcp-content-read-only",
        permissions=[],
    )
    read_client = _SecureClient(HTTP_AUTHORIZATION=f"Bearer {read_key.plaintext_token}")
    denied_script = _call(read_client, "jrc_save_script", {"post_id": str(post.pk), "markdown": "Nope"})
    denied_comment = _call(read_client, "jrc_add_comment", {"post_id": str(post.pk), "body": "Nope"})
    assert denied_script["error"]["data"]["status"] == 403
    assert denied_comment["error"]["data"]["status"] == 403
    assert not TextDocument.objects.filter(post=post).exists()
    assert not PostComment.objects.filter(post=post).exists()


@pytest.mark.django_db
def test_content_tools_hide_posts_outside_the_key_allowlist(owner_client, owner):
    from apps.social_accounts.models import SocialAccount

    _, workspace, user_membership, _, _, _ = owner
    foreign_account = SocialAccount.objects.create(
        workspace=workspace,
        platform="instagram",
        account_platform_id="ig-mcp-content-foreign",
        account_name="Foreign account",
        connection_status="connected",
    )
    foreign_post = Post.objects.create(workspace=workspace, author=user_membership.user, title="Foreign content")
    PlatformPost.objects.create(post=foreign_post, social_account=foreign_account, status="draft")

    result = _call(owner_client, "jrc_get_script", {"post_id": str(foreign_post.pk)})
    assert result["error"]["data"]["status"] == 404


@pytest.mark.django_db
def test_write_tools_reject_viewer_client_and_contributor_editing_others(owner):
    user, workspace, _, account, post, variant = owner
    viewer, _ = _role_client(workspace, account, user, WorkspaceMembership.WorkspaceRole.VIEWER, name="viewer")
    client, _ = _role_client(workspace, account, user, WorkspaceMembership.WorkspaceRole.CLIENT, name="client")
    contributor, _ = _role_client(
        workspace, account, user, WorkspaceMembership.WorkspaceRole.CONTRIBUTOR, name="contributor"
    )
    args_by_tool = {
        "jrc_save_script": {"post_id": str(post.pk), "markdown": "Rejected"},
        "jrc_save_caption": {
            "post_id": str(post.pk),
            "platform_post_id": str(variant.pk),
            "text": "Rejected",
        },
    }

    for role_client in (viewer, client):
        for tool, args in args_by_tool.items():
            result = _call(role_client, tool, args)
            assert result["error"]["data"]["status"] == 403

    # Contributor has permission to create content but cannot edit a post
    # authored by someone else.
    for tool, args in args_by_tool.items():
        result = _call(contributor, tool, args)
        assert result["error"]["data"]["status"] == 403

    assert not TextDocument.objects.filter(post=post).exists()
    assert not TextVersion.objects.exists()
    variant.refresh_from_db()
    assert variant.platform_specific_caption is None


@pytest.mark.django_db
def test_every_content_tool_returns_404_outside_workspace_or_key_allowlist(owner, owner_client):
    from apps.organizations.models import Organization
    from apps.social_accounts.models import SocialAccount
    from apps.workspaces.models import Workspace

    user, workspace, _, _, post, variant = owner
    foreign_account = SocialAccount.objects.create(
        workspace=workspace,
        platform="instagram",
        account_platform_id="ig-mcp-content-unlisted",
        account_name="Unlisted account",
        connection_status="connected",
    )
    unlisted_post = Post.objects.create(workspace=workspace, author=user, title="Unlisted content")
    unlisted_variant = PlatformPost.objects.create(post=unlisted_post, social_account=foreign_account, status="draft")

    foreign_organization = Organization.objects.create(name="Other MCP Content Org")
    foreign_workspace = Workspace.objects.create(name="Other MCP Content Workspace", organization=foreign_organization)
    OrgMembership.objects.create(user=user, organization=foreign_organization, org_role=OrgMembership.OrgRole.OWNER)
    WorkspaceMembership.objects.create(
        user=user,
        workspace=foreign_workspace,
        workspace_role=WorkspaceMembership.WorkspaceRole.OWNER,
    )
    foreign_workspace_account = SocialAccount.objects.create(
        workspace=foreign_workspace,
        platform="linkedin_personal",
        account_platform_id="li-mcp-content-foreign-workspace",
        account_name="Other workspace account",
        connection_status="connected",
    )
    foreign_workspace_post = Post.objects.create(
        workspace=foreign_workspace, author=user, title="Other workspace content"
    )
    foreign_workspace_variant = PlatformPost.objects.create(
        post=foreign_workspace_post, social_account=foreign_workspace_account, status="draft"
    )

    def args_for(target_post, target_variant):
        return {
            "jrc_get_script": {"post_id": str(target_post.pk)},
            "jrc_save_script": {"post_id": str(target_post.pk), "markdown": "No"},
            "jrc_get_caption": {
                "post_id": str(target_post.pk),
                "platform_post_id": str(target_variant.pk),
            },
            "jrc_save_caption": {
                "post_id": str(target_post.pk),
                "platform_post_id": str(target_variant.pk),
                "text": "No",
            },
            "jrc_list_comments": {"post_id": str(target_post.pk)},
            "jrc_add_comment": {"post_id": str(target_post.pk), "body": "No"},
            "jrc_get_stage": {"post_id": str(target_post.pk)},
        }

    targets = (
        (unlisted_post, unlisted_variant),
        (foreign_workspace_post, foreign_workspace_variant),
    )
    for target_post, target_variant in targets:
        for tool, args in args_for(target_post, target_variant).items():
            result = _call(owner_client, tool, args)
            assert result["error"]["data"]["status"] == 404, (tool, target_post.pk, result)

    # A channel copy from a different post is also outside the requested post.
    mismatch = _call(
        owner_client,
        "jrc_get_caption",
        {"post_id": str(post.pk), "platform_post_id": str(unlisted_variant.pk)},
    )
    assert mismatch["error"]["data"]["status"] == 404


@pytest.mark.django_db
def test_client_content_reads_hide_drafts_and_internal_comments(owner, owner_client):
    from apps.approvals.models import PostComment
    from apps.jrc_texts import services as text_services

    user, workspace, _, account, post, variant = owner
    client, client_user = _role_client(
        workspace, account, user, WorkspaceMembership.WorkspaceRole.CLIENT, name="read-client"
    )

    script = text_services.create_document(post.pk, user, markdown="# Saved script", source="mcp")
    script_draft = text_services.edit(script.versions.get(state=TextVersion.State.FINAL).pk, user, source="mcp")
    caption, _ = text_services.save_caption(post.pk, variant.pk, user, "Saved caption", source="mcp")
    caption_draft = text_services.edit(caption.pk, user, source="mcp")
    PostComment.objects.create(
        post=post,
        author=user,
        body="Internal only sentinel",
        visibility=PostComment.Visibility.INTERNAL,
    )

    client_script = _result(_call(client, "jrc_get_script", {"post_id": str(post.pk)}))
    client_caption = _result(
        _call(client, "jrc_get_caption", {"post_id": str(post.pk), "platform_post_id": str(variant.pk)})
    )
    client_comments = _result(_call(client, "jrc_list_comments", {"post_id": str(post.pk)}))

    assert client_script["markdown"] == "# Saved script"
    assert all(version["id"] != str(script_draft.pk) for version in client_script["versions"])
    assert "Internal only sentinel" not in json.dumps(client_script)
    assert client_caption["text"] == "Saved caption"
    assert all(version["id"] != str(caption_draft.pk) for version in client_caption["versions"])
    assert "Internal only sentinel" not in json.dumps(client_caption)
    assert client_comments == []
    assert client_user.pk != user.pk
