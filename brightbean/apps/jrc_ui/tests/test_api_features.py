# JR Content: přidáno 2026-10-23 (blok F2-B10), AGPL-3.0.
import json
from dataclasses import replace
from unittest.mock import Mock, patch
from uuid import uuid4

import pytest
from django.core.cache import cache
from django.test import Client

from apps.api_keys.services import issue_api_key
from apps.jrc_ui.features import MVP_FEATURES
from apps.mcp import tools
from apps.mcp.protocol import INVALID_PARAMS
from apps.members.models import PERMISSION_KEYS, WorkspaceMembership
from apps.social_accounts.models import SocialAccount
from apps.workspaces.models import Workspace

pytestmark = pytest.mark.django_db

# Nezávisle na implementačním seznamu: všechny současné vstupy vypnutých funkcí.
HIDDEN_TOOLS = (
    "list_inbox_messages",
    "get_inbox_message",
    "create_reply_draft",
    "update_reply_draft",
    "discard_reply_draft",
    "send_reply",
    "get_account_analytics",
    "get_post_analytics",
)
REST_OPERATIONS = (
    ("get", "/inbox/"),
    ("get", "/inbox/{id}"),
    ("post", "/inbox/{id}/replies"),
    ("patch", "/inbox/replies/{id}"),
    ("post", "/inbox/replies/{id}/send"),
    ("delete", "/inbox/replies/{id}"),
    ("get", "/analytics/accounts/{id}"),
    ("get", "/analytics/posts/{id}"),
)


@pytest.fixture(autouse=True)
def mvp_settings(settings):
    settings.JRC_FEATURES = MVP_FEATURES.copy()
    cache.delete("agent_api:auth_fail:127.0.0.1")


@pytest.fixture
def api_account(organization, org_owner):
    workspace = Workspace.objects.create(organization=organization, name="Local API")
    WorkspaceMembership.objects.create(user=org_owner, workspace=workspace, workspace_role="owner")
    return SocialAccount.objects.create(
        workspace=workspace,
        platform="facebook",
        account_platform_id="local-feature-test",
        account_name="Local test account",
        connection_status="connected",
    )


@pytest.fixture
def api_client(api_account, org_owner):
    key = issue_api_key(
        workspace=api_account.workspace,
        social_accounts=[api_account],
        issued_by=org_owner,
        name="Local feature test",
        permissions=list(PERMISSION_KEYS),
    )
    return Client(HTTP_AUTHORIZATION=f"Bearer {key.plaintext_token}")


@pytest.fixture
def handlers(monkeypatch):
    # Špioni prokazují odmítnutí ještě před vstupem do handleru, nikoli až
    # validací argumentů nebo chybou oprávnění uvnitř skryté funkce.
    spies = {}
    for name in HIDDEN_TOOLS:
        handler = Mock(return_value={"content": []})
        monkeypatch.setitem(tools._REGISTRY, name, replace(tools._REGISTRY[name], handler=handler))
        spies[name] = handler
    return spies


def rpc(method, params=None, *, notification=False):
    message = {"jsonrpc": "2.0", "method": method, "params": params or {}}
    if not notification:
        message["id"] = 1
    return message


def post_rpc(client, body, path="/api/v1/mcp/"):
    return client.post(path, json.dumps(body), content_type="application/json", secure=True)


@pytest.mark.parametrize("path", ["/api/v1/mcp", "/api/v1/mcp/"])
def test_mcp_catalog_hides_disabled_functions_and_keeps_local_tools(api_client, path):
    response = post_rpc(api_client, rpc("tools/list"), path)
    assert response.status_code == 200
    names = {tool["name"] for tool in response.json()["result"]["tools"]}
    assert names.isdisjoint(HIDDEN_TOOLS)
    assert {"list_accounts", "create_draft", "list_posts", "search_media", "upload_media"} <= names
    # Katalog i skutečné volání ponechají povolené nástroje funkční.
    response = post_rpc(api_client, rpc("tools/call", {"name": "list_accounts"}), path)
    assert "result" in response.json()


@pytest.mark.parametrize("path", ["/api/v1/mcp", "/api/v1/mcp/"])
@pytest.mark.parametrize("name", HIDDEN_TOOLS)
def test_mcp_call_cannot_execute_hidden_handler(api_client, handlers, name, path):
    response = post_rpc(api_client, rpc("tools/call", {"name": name}), path)
    assert response.status_code == 200
    assert response.json()["error"]["code"] == INVALID_PARAMS
    assert "unknown tool" in response.json()["error"]["message"]
    handlers[name].assert_not_called()


@pytest.mark.parametrize("notification", [False, True])
def test_mcp_batches_and_notifications_cannot_execute_hidden_handlers(api_client, handlers, notification):
    messages = [rpc("tools/call", {"name": name}, notification=notification) for name in HIDDEN_TOOLS]
    # I notifikace procházejí stejným filtrem, přestože nevracejí JSON-RPC odpověď.
    response = post_rpc(api_client, messages)
    if notification:
        assert response.status_code == 202
        assert response.content == b""
    else:
        assert response.status_code == 200
        assert len(response.json()) == len(HIDDEN_TOOLS)
        assert all(item["error"]["code"] == INVALID_PARAMS for item in response.json())
    for handler in handlers.values():
        handler.assert_not_called()


@pytest.mark.parametrize("method,path", REST_OPERATIONS)
@pytest.mark.parametrize("authenticated", [False, True])
def test_rest_hidden_operations_return_404_before_auth_and_side_effects(api_client, method, path, authenticated):
    client = api_client if authenticated else Client(enforce_csrf_checks=True)
    with patch("apps.inbox.services._dispatch_to_platform") as dispatch:
        response = getattr(client, method)("/api/v1" + path.format(id=uuid4()), secure=True)
    assert response.status_code == 404
    dispatch.assert_not_called()


@pytest.mark.parametrize("method,path", REST_OPERATIONS)
def test_rest_guard_also_works_without_feature_middleware(api_client, settings, method, path):
    settings.MIDDLEWARE = [m for m in settings.MIDDLEWARE if m != "apps.jrc_ui.middleware.FeatureMiddleware"]
    response = getattr(api_client, method)("/api/v1" + path.format(id=uuid4()), secure=True)
    assert response.status_code == 404


@pytest.mark.parametrize(
    "feature,names,rest_path",
    [
        ("inbox", HIDDEN_TOOLS[:6], "/api/v1/inbox/"),
        ("analytics", HIDDEN_TOOLS[6:], "/api/v1/analytics/accounts/{id}"),
    ],
)
def test_same_switch_controls_web_rest_and_mcp_without_restart(
    api_client, api_account, handlers, settings, feature, names, rest_path
):
    web_path = f"/workspace/{api_account.workspace_id}/{feature}/"
    rest_path = rest_path.format(id=api_account.id)
    for enabled in (True, False):
        settings.JRC_FEATURES = {**MVP_FEATURES, feature: enabled}
        response = post_rpc(api_client, rpc("tools/list"))
        catalog = {tool["name"] for tool in response.json()["result"]["tools"]}
        assert set(names) <= catalog if enabled else set(names).isdisjoint(catalog)
        other_names = set(HIDDEN_TOOLS) - set(names)
        assert other_names.isdisjoint(catalog)
        for name in names:
            # Platná schémata pro všechny nástroje; při povolení běží špion místo logiky.
            item_id = str(uuid4())
            arguments = {
                "list_inbox_messages": {},
                "get_inbox_message": {"message_id": item_id},
                "create_reply_draft": {"message_id": item_id, "body": "Local reply"},
                "update_reply_draft": {"reply_id": item_id, "body": "Local reply"},
                "discard_reply_draft": {"reply_id": item_id},
                "send_reply": {"reply_id": item_id},
                "get_account_analytics": {"account_id": item_id},
                "get_post_analytics": {"post_id": item_id},
            }[name]
            response = post_rpc(api_client, rpc("tools/call", {"name": name, "arguments": arguments}))
            assert ("result" in response.json()) == enabled
            assert handlers[name].call_count == 1
        # Žádný fiktivní webový účet není přihlášený; zapnutý web se přesměruje na login.
        assert api_client.get(web_path, secure=True).status_code == (302 if enabled else 404)
        # Povolené REST funkce skutečně fungují se stejným platným klíčem.
        response = api_client.get(rest_path, secure=True)
        assert response.status_code == (200 if enabled else 404)
