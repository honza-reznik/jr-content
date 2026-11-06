"""Inventory and negative HTTP matrix for every registered browser writer."""

import re
from types import SimpleNamespace
from uuid import uuid4

import pytest
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied
from django.test import RequestFactory
from django.urls import URLResolver, get_resolver, resolve, reverse

from apps.accounts.models import User
from apps.composer.models import PlatformPost, Post
from apps.jrc_ui.write_access import DELEGATED_WRITES, READ_VIEWS, WRITE_RULES, WriteAccessMiddleware
from apps.members.models import BUILTIN_ROLE_PERMISSIONS, CustomRole, WorkspaceMembership
from apps.social_accounts.models import SocialAccount
from apps.workspaces.models import Workspace

ROLES = [*BUILTIN_ROLE_PERMISSIONS, "nonmember"]


def registered_app_urls(patterns=None, namespace="", prefix=""):
    for pattern in patterns or get_resolver().url_patterns:
        if isinstance(pattern, URLResolver):
            yield from registered_app_urls(
                pattern.url_patterns,
                ":".join(filter(None, [namespace, pattern.namespace])),
                prefix + str(pattern.pattern),
            )
        elif getattr(pattern.callback, "__module__", "").startswith("apps."):
            key = ":".join(filter(None, [namespace, pattern.name]))
            if not pattern.name:
                key = f"{pattern.callback.__module__}.{pattern.callback.__name__}"
            yield key, prefix + str(pattern.pattern), pattern.callback


def test_every_registered_writer_has_an_audited_policy():
    unknown = []
    for key, path, _callback in registered_app_urls():
        if key not in WRITE_RULES and key not in DELEGATED_WRITES and key not in READ_VIEWS:
            unknown.append((key, path))
    assert not unknown, f"Review and register write permissions for: {unknown}"
    # The inventory is derived from the resolver, never a hand-written URL list.
    registered = {key for key, _, _ in registered_app_urls()}
    assert not set(WRITE_RULES) - registered


@pytest.fixture
def workspace(organization, org_owner):
    ws = Workspace.objects.create(organization=organization, name="Write audit")
    WorkspaceMembership.objects.create(workspace=ws, user=org_owner, workspace_role="owner")
    return ws


@pytest.mark.django_db
@pytest.mark.parametrize("role", ROLES)
@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
def test_registered_workspace_writes_reject_missing_permission(workspace, user, role, method):
    """Call each registered URL's actual shared gate, before its handler can run."""
    membership = WorkspaceMembership.objects.get(workspace=workspace, user=user)
    if role == "nonmember":
        membership = None
    else:
        membership.workspace_role = role
        membership.save()
    factory = RequestFactory()
    middleware = WriteAccessMiddleware(lambda request: None)
    covered = []
    for key, path, callback in registered_app_urls():
        rule = WRITE_RULES.get(key)
        if not rule or (method == "POST" and rule.domain in {"service", "connection_token"}):
            continue  # Domain writers are exercised by the HTTP tests below and their own suites.
        kwargs = {}
        for converter, name in re.findall(r"<([^:>]+):([^>]+)>", path):
            kwargs[name] = workspace.pk if name == "workspace_id" else uuid4() if converter == "uuid" else "example"
        url = reverse(key, kwargs=kwargs)
        request = factory.generic(method, url)
        request.user = user
        request.workspace_membership = membership
        request.session = {}
        request.resolver_match = resolve(url)
        if method != "POST":
            assert middleware.process_view(request, callback, (), kwargs).status_code == 405
            covered.append(key)
        elif (
            not membership
            or any(not membership.effective_permissions.get(p, False) for p in rule.permissions)
            or (rule.domain == "team_review" and role in ("client", "viewer"))
            or (rule.domain == "transition" and role in ("client", "viewer"))
        ):
            with pytest.raises(PermissionDenied):
                middleware.process_view(request, callback, (), kwargs)
            covered.append(key)
    if role not in ("owner", "manager") or method != "POST":
        assert covered


@pytest.mark.django_db
@pytest.mark.parametrize("role", ["client", "viewer", "contributor", "nonmember"])
@pytest.mark.parametrize("channel", [False, True])
def test_delete_and_channel_delete_cannot_mutate_foreign_content(client, workspace, user, role, channel):
    author = User.objects.create_user(email="author@example.invalid")
    post = Post.objects.create(workspace=workspace, author=author, title="Keep this post")
    account = SocialAccount.objects.create(workspace=workspace, platform="instagram", account_name="Local")
    variant = PlatformPost.objects.create(post=post, social_account=account)
    membership = WorkspaceMembership.objects.get(workspace=workspace, user=user)
    if role == "nonmember":
        membership.delete()
    else:
        membership.workspace_role = role
        membership.save()
    client.force_login(user)
    url = reverse("composer:post_delete", args=[workspace.pk, post.pk])
    if channel:
        url += f"?account={account.pk}"
    assert client.post(url, HTTP_HX_REQUEST="true").status_code == 403
    assert Post.objects.filter(pk=post.pk).exists()
    assert PlatformPost.objects.filter(pk=variant.pk).exists()


@pytest.mark.django_db
@pytest.mark.parametrize("role", ["owner", "manager", "editor", "contributor"])
def test_authorized_delete_still_works(client, workspace, user, role):
    WorkspaceMembership.objects.filter(workspace=workspace, user=user).update(workspace_role=role)
    post = Post.objects.create(workspace=workspace, author=user, title="Own draft")
    client.force_login(user)
    assert client.post(reverse("composer:post_delete", args=[workspace.pk, post.pk])).status_code == 204
    assert not Post.objects.filter(pk=post.pk).exists()


@pytest.mark.django_db
def test_custom_role_and_author_cannot_bypass_missing_create_permission(client, workspace, user):
    custom = CustomRole.objects.create(
        organization=workspace.organization, name="No writes", permissions={"edit_others_posts": True}
    )
    membership = WorkspaceMembership.objects.get(workspace=workspace, user=user)
    membership.custom_role = custom
    membership.save()
    post = Post.objects.create(workspace=workspace, author=user, title="Own protected draft")
    client.force_login(user)
    assert client.post(reverse("composer:post_delete", args=[workspace.pk, post.pk])).status_code == 403
    assert Post.objects.filter(pk=post.pk).exists()


@pytest.mark.django_db
@pytest.mark.parametrize("role", ["client", "viewer", "contributor", "editor", "nonmember"])
def test_domain_writers_reject_role_at_real_http_boundary(client, workspace, user, role):
    post = Post.objects.create(
        workspace=workspace, author=User.objects.create_user(email="owner2@example.invalid"), title="Protected"
    )
    membership = WorkspaceMembership.objects.get(workspace=workspace, user=user)
    if role == "nonmember":
        membership.delete()
    else:
        membership.workspace_role = role
        membership.save()
    client.force_login(user)
    before = (post.title, post.caption, post.proposed_publish_at)
    for key, path, _callback in registered_app_urls():
        rule = WRITE_RULES.get(key)
        if not rule or rule.domain != "service" or "<uuid:workspace_id>" not in path:
            continue
        # Editors can write content; comments are deliberately available to readers.
        if role == "editor" or key.endswith(("add_content_comment", "reply_to_content_comment")):
            continue
        if key in (
            "jrc_production:move",
            "jrc_production:content_detail",
            "jrc_production:mark_published",
            "jrc_production:approval_decision",
            "jrc_production:resolve_content_comment",
        ):
            continue  # Action-dependent authorization is tested in production's role suite.
        kwargs = {
            name: workspace.pk if name == "workspace_id" else post.pk if name == "post_id" else uuid4()
            for name in re.findall(r"<uuid:([^>]+)>", path)
        }
        if key == "jrc_production:shotlist_action":
            response = client.post(reverse(key, kwargs=kwargs), {"action": "add", "text": "Forbidden shot"})
        else:
            response = client.post(
                reverse(key, kwargs=kwargs), {"action": "edit", "content": {}}, content_type="application/json"
            )
        assert response.status_code in (403, 404), (role, key, response.status_code, response.content[:200])
    post.refresh_from_db()
    assert (post.title, post.caption, post.proposed_publish_at) == before


@pytest.mark.django_db
def test_reviewed_read_views_refuse_every_unsafe_method(user):
    """A reviewed read view never reaches its handler with a write (405, not a silent 200)."""
    middleware = WriteAccessMiddleware(lambda request: None)
    covered = 0
    for key, path, callback in registered_app_urls():
        if key not in READ_VIEWS:
            continue
        for method in ("POST", "PUT", "PATCH", "DELETE"):
            request = RequestFactory().generic(method, "/" + path)
            request.user = user
            request.resolver_match = SimpleNamespace(view_name=key)
            response = middleware.process_view(request, callback, (), {})
            assert response.status_code == 405, (key, method)
        covered += 1
    assert covered


def test_new_unregistered_app_write_fails_closed():
    request = RequestFactory().post("/new-write/")
    request.user = AnonymousUser()
    request.resolver_match = SimpleNamespace(view_name="composer:new_unreviewed_write")
    callback = SimpleNamespace(__module__="apps.composer.views")
    with pytest.raises(PermissionDenied):
        WriteAccessMiddleware(lambda request: None).process_view(request, callback, (), {})


def url_kwargs(path, workspace, post=None):
    return {
        name: workspace.pk
        if name == "workspace_id"
        else post.pk
        if name == "post_id" and post
        else uuid4()
        if converter == "uuid"
        else "example"
        for converter, name in re.findall(r"<([^:>]+):([^>]+)>", path)
    }


@pytest.mark.django_db
@pytest.mark.parametrize("role", ROLES)
def test_role_denials_through_registered_http_urls(client, workspace, user, role):
    membership = WorkspaceMembership.objects.get(workspace=workspace, user=user)
    if role == "nonmember":
        membership.delete()
        permissions = {}
    else:
        membership.workspace_role = role
        membership.save()
        permissions = membership.effective_permissions
    client.force_login(user)
    for key, path, _callback in registered_app_urls():
        rule = WRITE_RULES.get(key)
        if not rule or rule.domain in {"service", "connection_token"}:
            continue
        denied = role == "nonmember" or any(not permissions.get(p, False) for p in rule.permissions)
        denied |= rule.domain == "team_review" and role in ("client", "viewer")
        if denied:
            response = client.post(reverse(key, kwargs=url_kwargs(path, workspace)))
            assert response.status_code in (403, 404), (role, key, response.status_code)


@pytest.mark.django_db
@pytest.mark.parametrize("role", ["client", "viewer", "contributor"])
def test_all_foreign_post_writes_are_denied_before_payload_processing(client, workspace, user, role):
    WorkspaceMembership.objects.filter(workspace=workspace, user=user).update(workspace_role=role)
    post = Post.objects.create(
        workspace=workspace, author=User.objects.create_user(email="foreign@example.invalid"), title="Untouched"
    )
    client.force_login(user)
    for key, path, _callback in registered_app_urls():
        rule = WRITE_RULES.get(key)
        if not rule or rule.owned_object != "post":
            continue
        response = client.post(reverse(key, kwargs=url_kwargs(path, workspace, post)))
        assert response.status_code == 403, (role, key, response.status_code)
    post.refresh_from_db()
    assert post.title == "Untouched"


@pytest.mark.django_db
@pytest.mark.parametrize("role", ["client", "viewer", "contributor"])
@pytest.mark.parametrize("target", ["draft", "pending_review", "approved", "scheduled", "published"])
def test_chip_cannot_bypass_author_or_approval_role(client, workspace, user, role, target):
    WorkspaceMembership.objects.filter(workspace=workspace, user=user).update(workspace_role=role)
    post = Post.objects.create(workspace=workspace, author=User.objects.create_user(email="chip-owner@example.invalid"))
    account = SocialAccount.objects.create(workspace=workspace, platform="instagram", account_name="Test")
    variant = PlatformPost.objects.create(post=post, social_account=account)
    client.force_login(user)
    response = client.post(
        reverse("composer:transition_platform_post", args=[workspace.pk, post.pk, variant.pk]),
        {"target_status": target},
    )
    assert response.status_code == 403
    variant.refresh_from_db()
    assert variant.status == "draft"


@pytest.mark.django_db
def test_nonmember_404_keeps_application_layout(client, workspace, user):
    WorkspaceMembership.objects.filter(workspace=workspace, user=user).delete()
    client.force_login(user)
    response = client.get(reverse("jrc_production:content_detail", args=[workspace.pk, uuid4()]))
    assert response.status_code == 404
    assert b"This page is unavailable" in response.content
    assert b"Back to content board" in response.content


@pytest.mark.django_db
@pytest.mark.parametrize("operation", ["update", "schedule", "cancel", "mcp_cancel", "mcp_schedule"])
def test_api_and_mcp_enforce_foreign_post_ownership(client, workspace, user, operation):
    from apps.api.auth import VirtualMembership
    from apps.api_keys.services import issue_api_key
    from apps.mcp.handlers import _cancel_post, _schedule_draft
    from apps.mcp.protocol import JsonRpcError

    account = SocialAccount.objects.create(workspace=workspace, platform="instagram", account_name="Test")
    post = Post.objects.create(workspace=workspace, author=User.objects.create_user(email="rest-owner@example.invalid"))
    PlatformPost.objects.create(post=post, social_account=account)
    issued = issue_api_key(
        workspace=workspace,
        social_accounts=[account],
        issued_by=user,
        name="Audit",
        permissions=["create_posts", "publish_directly"],
    )
    custom = CustomRole.objects.create(
        organization=workspace.organization,
        name="Own content publisher",
        permissions={"create_posts": True, "publish_directly": True},
    )
    WorkspaceMembership.objects.filter(workspace=workspace, user=user).update(custom_role=custom)
    # Simulate a narrowed credential; edit_others_posts is deliberately absent.
    membership = VirtualMembership({"create_posts": True, "publish_directly": True}, workspace, user)
    if operation.startswith("mcp_"):
        handler = _cancel_post if operation == "mcp_cancel" else _schedule_draft
        context = {"membership": membership, "api_key": issued.api_key, "workspace": workspace}
        with pytest.raises(JsonRpcError, match="cannot change"):
            handler({"post_id": str(post.pk), "scheduled_at": "2030-01-01T12:00:00Z"}, context)
    else:
        suffix = "" if operation == "update" else f"/{operation}"
        payload = {"title": "Forbidden"} if operation == "update" else {"scheduled_at": "2030-01-01T12:00:00Z"}
        method = client.patch if operation == "update" else client.post
        response = method(
            f"/api/v1/posts/{post.pk}{suffix}",
            payload,
            content_type="application/json",
            secure=True,
            HTTP_AUTHORIZATION=f"Bearer {issued.plaintext_token}",
        )
        assert response.status_code == 403, response.content


# Method + path are checked against the generated OpenAPI document. A new
# mutation needs an audited policy and joins the role matrix automatically.
API_WRITE_POLICIES = {
    ("post", "/posts/"): ("create_posts",),
    ("patch", "/posts/{post_id}"): ("create_posts",),
    ("post", "/posts/{post_id}/schedule"): ("create_posts", "publish_directly"),
    ("post", "/posts/{post_id}/cancel"): ("create_posts",),
    ("post", "/posts/{post_id}/script"): ("create_posts",),
    ("post", "/posts/{post_id}/concept"): ("create_posts",),
    ("post", "/posts/{post_id}/captions/{platform_post_id}"): ("create_posts",),
    ("patch", "/posts/{post_id}/captions/{platform_post_id}"): ("create_posts",),
    ("post", "/posts/{post_id}/stage"): ("create_posts|approve_posts",),
    ("post", "/posts/{post_id}/comments"): ("create_posts|approve_posts",),
    ("post", "/posts/{post_id}/channels"): ("create_posts",),
    ("delete", "/posts/{post_id}/channels/{platform_post_id}"): ("create_posts",),
    ("post", "/posts/{post_id}/shotlist"): ("create_posts",),
    ("patch", "/posts/{post_id}/shotlist/{item_id}"): ("create_posts",),
    ("delete", "/posts/{post_id}/shotlist/{item_id}"): ("create_posts",),
    ("post", "/media/"): ("upload_media",),
    ("post", "/inbox/{message_id}/replies"): ("use_inbox",),
    ("patch", "/inbox/replies/{reply_id}"): ("use_inbox",),
    ("delete", "/inbox/replies/{reply_id}"): ("use_inbox",),
    ("post", "/inbox/replies/{reply_id}/send"): ("reply_from_inbox",),
    ("post", "/mcp/"): (),  # Each tool has its own policy below.
}
MCP_WRITE_POLICIES = {
    "cancel_post": ("create_posts",),
    "create_draft": ("create_posts",),
    "schedule_draft": ("create_posts", "publish_directly"),
    "schedule_post": ("create_posts", "publish_directly"),
    "upload_media": ("upload_media",),
    "request_media_upload": ("upload_media",),
    "finalize_media_upload": ("upload_media",),
    "jrc_save_script": ("create_posts",),
    "jrc_save_concept": ("create_posts",),
    "jrc_save_caption": ("create_posts",),
    "jrc_add_comment": ("create_posts|approve_posts",),
    "create_reply_draft": ("use_inbox",),
    "update_reply_draft": ("use_inbox",),
    "discard_reply_draft": ("use_inbox",),
    "send_reply": ("reply_from_inbox",),
}


def api_writes():
    from apps.api.api import api

    for path, operations in api.get_openapi_schema()["paths"].items():
        for method in operations:
            if method in ("post", "put", "patch", "delete"):
                yield method, path.split("/api/v1", 1)[1]


def test_all_agent_writers_have_audited_policies(settings):
    from apps.mcp.tools import all_tools

    settings.JRC_FEATURES = {}
    assert set(api_writes()) == set(API_WRITE_POLICIES)
    read_tools = {
        "jrc_get_script",
        "jrc_get_concept",
        "jrc_get_caption",
        "jrc_list_comments",
        "jrc_get_stage",
        "list_accounts",
        "get_post",
        "list_posts",
        "search_media",
        "get_media",
        "get_account_analytics",
        "get_post_analytics",
        "list_inbox_messages",
        "get_inbox_message",
    }
    assert {tool.name for tool in all_tools()} == set(MCP_WRITE_POLICIES) | read_tools
    assert {tool.name for tool in all_tools() if tool.read_only} == read_tools


def missing_permission(permissions, required):
    return any(not any(permissions.get(key, False) for key in group.split("|")) for group in required)


@pytest.mark.django_db
@pytest.mark.parametrize("role", ROLES)
def test_generated_rest_role_denials(client, workspace, user, role, settings):
    from django.core.cache import cache
    from django.core.files.uploadedfile import SimpleUploadedFile

    from apps.api_keys.services import issue_api_key
    from apps.members.models import PERMISSION_KEYS

    settings.JRC_FEATURES = {}
    cache.clear()
    account = SocialAccount.objects.create(workspace=workspace, platform="instagram", account_name="Test")
    post = Post.objects.create(workspace=workspace, author=user, title="Keep")
    variant = PlatformPost.objects.create(post=post, social_account=account)
    issued = issue_api_key(
        workspace=workspace, social_accounts=[account], issued_by=user, name="Matrix", permissions=list(PERMISSION_KEYS)
    )
    membership = WorkspaceMembership.objects.get(workspace=workspace, user=user)
    if role == "nonmember":
        membership.delete()
        permissions = {}
    else:
        membership.workspace_role = role
        membership.save()
        permissions = membership.effective_permissions
    payload = {
        "title": "Forbidden",
        "caption": "Forbidden",
        "text": "Forbidden",
        "body": "Forbidden",
        "markdown": "Forbidden",
        "social_account_id": str(account.pk),
        "scheduled_at": "2030-01-01T12:00:00Z",
        "stage": "script",
        "expected_stage": "idea",
    }
    ids = dict(post_id=post.pk, platform_post_id=variant.pk, item_id=uuid4(), message_id=uuid4(), reply_id=uuid4())
    for method, pattern in api_writes():
        if pattern == "/mcp/" or (
            role != "nonmember" and not missing_permission(permissions, API_WRITE_POLICIES[method, pattern])
        ):
            continue
        path = "/api/v1" + pattern.format(**ids)
        kwargs = dict(secure=True, HTTP_AUTHORIZATION=f"Bearer {issued.plaintext_token}")
        if pattern == "/media/":
            response = client.post(path, {"file": SimpleUploadedFile("test.txt", b"audit")}, **kwargs)
        else:
            response = getattr(client, method)(path, payload, content_type="application/json", **kwargs)
        assert response.status_code == (401 if role == "nonmember" else 403), (
            role,
            method,
            pattern,
            response.status_code,
            response.content[:150],
        )
    post.refresh_from_db()
    variant.refresh_from_db()
    assert post.title == "Keep"
    assert variant.status == "draft"


@pytest.mark.django_db
@pytest.mark.parametrize("role", ROLES)
def test_generated_mcp_role_denials(client, workspace, user, role, settings):
    from django.core.cache import cache

    from apps.api_keys.services import issue_api_key
    from apps.mcp.tools import all_tools
    from apps.members.models import PERMISSION_KEYS

    settings.JRC_FEATURES = {}
    cache.clear()
    account = SocialAccount.objects.create(workspace=workspace, platform="instagram", account_name="Test")
    post = Post.objects.create(workspace=workspace, author=user, title="Keep")
    variant = PlatformPost.objects.create(post=post, social_account=account)
    issued = issue_api_key(
        workspace=workspace,
        social_accounts=[account],
        issued_by=user,
        name="MCP matrix",
        permissions=list(PERMISSION_KEYS),
    )
    membership = WorkspaceMembership.objects.get(workspace=workspace, user=user)
    if role == "nonmember":
        membership.delete()
        permissions = {}
    else:
        membership.workspace_role = role
        membership.save()
        permissions = membership.effective_permissions
    values = dict(
        post_id=str(post.pk),
        platform_post_id=str(variant.pk),
        social_account_id=str(account.pk),
        message_id=str(uuid4()),
        reply_id=str(uuid4()),
        upload_id=str(uuid4()),
        body="Forbidden",
        text="Forbidden",
        markdown="Forbidden",
        caption="Forbidden",
        scheduled_at="2030-01-01T12:00:00Z",
        filename="test.png",
        content_base64="YQ==",
        media_type="image",
    )
    for tool in all_tools():
        if tool.read_only or (
            role != "nonmember" and not missing_permission(permissions, MCP_WRITE_POLICIES[tool.name])
        ):
            continue
        arguments = {key: value for key, value in values.items() if key in tool.input_schema.get("properties", {})}
        if tool.name == "send_reply":
            arguments = {"reply_id": values["reply_id"]}
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": tool.name, "arguments": arguments},
        }
        response = client.post(
            "/api/v1/mcp/",
            payload,
            content_type="application/json",
            secure=True,
            HTTP_AUTHORIZATION=f"Bearer {issued.plaintext_token}",
        )
        if role == "nonmember":
            assert response.status_code == 401
        else:
            assert response.status_code == 200
            body = response.json()
            message = body.get("error", {}).get("message", "").lower()
            assert "permission denied" in message or "cannot edit" in message, (role, tool.name, body)
    post.refresh_from_db()
    variant.refresh_from_db()
    assert post.title == "Keep"
    assert variant.status == "draft"


@pytest.mark.django_db
@pytest.mark.parametrize("state", ["active", "expired", "revoked", "archived"])
def test_public_connection_writes_require_active_token(client, workspace, user, state, settings):
    from datetime import timedelta

    from django.utils import timezone

    from apps.onboarding.models import ConnectionLink

    settings.JRC_FEATURES = {}
    link = ConnectionLink.objects.create(
        workspace=workspace, created_by=user, expires_at=timezone.now() + timedelta(hours=1)
    )
    if state == "expired":
        link.expires_at = timezone.now() - timedelta(seconds=1)
        link.save()
    elif state == "revoked":
        link.revoked_at = timezone.now()
        link.save()
    elif state == "archived":
        workspace.is_archived = True
        workspace.save()
    for key, rule in WRITE_RULES.items():
        if rule.domain != "connection_token":
            continue
        url = reverse(key, kwargs={"token": link.token})
        request = RequestFactory().post(url)
        request.resolver_match = resolve(url)
        request.user = AnonymousUser()
        gate = WriteAccessMiddleware(lambda request: None)
        if state == "active":
            assert gate.process_view(request, resolve(url).func, (), {"token": link.token}) is None
        else:
            response = client.post(url)
            assert response.status_code == 403


@pytest.mark.django_db
@pytest.mark.parametrize("endpoint", ["idea_upload_media", "idea_create", "idea_edit", "save_post", "save_post_edit"])
def test_custom_text_writer_cannot_upload_via_another_form(client, workspace, user, endpoint):
    from django.core.files.uploadedfile import SimpleUploadedFile

    from apps.composer.models import Idea

    custom = CustomRole.objects.create(
        organization=workspace.organization, name="Text only", permissions={"create_posts": True}
    )
    WorkspaceMembership.objects.filter(workspace=workspace, user=user).update(custom_role=custom)
    kwargs = {"workspace_id": workspace.pk}
    if endpoint == "idea_edit":
        kwargs["idea_id"] = Idea.objects.create(workspace=workspace, author=user, title="Keep").pk
    elif endpoint == "save_post_edit":
        kwargs["post_id"] = Post.objects.create(workspace=workspace, author=user, title="Keep").pk
    client.force_login(user)
    response = client.post(
        reverse(f"composer:{endpoint}", kwargs=kwargs), {"file": SimpleUploadedFile("test.png", b"audit")}
    )
    assert response.status_code == 403


@pytest.mark.django_db
@pytest.mark.parametrize("owned,pending", [(True, True), (False, True), (True, False)])
def test_pending_media_delete_is_limited_to_own_session_upload(client, workspace, user, owned, pending):
    from apps.media_library.models import MediaAsset

    other = User.objects.create_user(email="media-other@example.invalid")
    asset = MediaAsset.objects.create(
        organization=workspace.organization,
        workspace=workspace,
        uploaded_by=user if owned else other,
        filename="pending.png",
        media_type="image",
    )
    client.force_login(user)
    session = client.session
    session[f"pending_media_{workspace.pk}"] = [str(asset.pk)] if pending else []
    session.save()
    response = client.post(reverse("composer:remove_pending_media", args=[workspace.pk, asset.pk]))
    if owned and pending:
        assert response.status_code == 200
        assert not MediaAsset.objects.filter(pk=asset.pk).exists()
    else:
        assert response.status_code == 403
        assert MediaAsset.objects.filter(pk=asset.pk).exists()


@pytest.mark.django_db
def test_invalid_connection_tokens_cannot_write(client, settings):
    settings.JRC_FEATURES = {}
    for key, rule in WRITE_RULES.items():
        if rule.domain == "connection_token":
            assert client.post(reverse(key, kwargs={"token": "invalid-audit-token"})).status_code == 404


@pytest.mark.django_db
@pytest.mark.parametrize("role", ["client", "viewer"])
def test_custom_client_role_cannot_bypass_workflow_through_chip(client, workspace, user, role):
    custom = CustomRole.objects.create(
        organization=workspace.organization,
        name="Extra client grants",
        permissions={"create_posts": True, "approve_posts": True},
    )
    WorkspaceMembership.objects.filter(workspace=workspace, user=user).update(workspace_role=role, custom_role=custom)
    post = Post.objects.create(workspace=workspace, author=user)
    account = SocialAccount.objects.create(workspace=workspace, platform="instagram", account_name="Test")
    variant = PlatformPost.objects.create(post=post, social_account=account, status="pending_review")
    client.force_login(user)
    response = client.post(
        reverse("composer:transition_platform_post", args=[workspace.pk, post.pk, variant.pk]),
        {"target_status": "approved"},
    )
    assert response.status_code == 403
    variant.refresh_from_db()
    assert variant.status == "pending_review"
