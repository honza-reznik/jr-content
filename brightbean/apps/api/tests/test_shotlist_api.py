# JR Content: přidáno 2026-11-04 (blok P1-B09), AGPL-3.0.
"""REST API for the shotlist of a content (P1-B09)."""

import pytest
from django.core.cache import cache
from django.test import Client, override_settings

from apps.accounts.models import User
from apps.api.tests.test_content_api import ROLES, api, make_key
from apps.composer.models import Post
from apps.jrc_channels.services import create_manual_channel
from apps.jrc_production.models import ShotlistChange, ShotlistItem
from apps.jrc_production.services import create_content
from apps.members.models import WorkspaceMembership
from apps.workspaces.models import Workspace

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _jrc_mode():
    cache.delete("agent_api:auth_fail:127.0.0.1")
    with override_settings(JRC_SOCIAL_PROVIDERS_ENABLED=False):
        yield


@pytest.fixture
def workspace(organization, user):
    workspace = Workspace.objects.create(organization=organization, name="Shotlist API")
    WorkspaceMembership.objects.create(workspace=workspace, user=user, workspace_role="owner")
    return workspace


@pytest.fixture
def content(workspace, user):
    channel = create_manual_channel(workspace, "instagram", "IG")
    return create_content(workspace, "API content", user, social_account_ids=[channel.pk])


@pytest.fixture
def key(workspace, user, org_owner, content):
    return make_key(workspace, user)


def test_read_add_update_move_delete(key, content):
    listing = api(key, "get", f"{content.pk}/shotlist")
    assert listing.status_code == 200
    body = listing.json()
    assert body["can_edit"] is True and (body["done"], body["total"]) == (0, 1)
    thumbnail = body["items"][0]
    assert (thumbnail["kind"], thumbnail["text"], thumbnail["updated_by"]) == ("thumbnail", "Thumbnail photo", None)

    added = api(key, "post", f"{content.pk}/shotlist", {"text": "Wide shot", "note": "Tripod"})
    assert added.status_code == 201
    item = added.json()["item"]
    assert (item["text"], item["note"], item["position"], item["is_done"]) == ("Wide shot", "Tripod", 1, False)
    assert item["updated_by"]

    done = api(key, "patch", f"{content.pk}/shotlist/{item['id']}", {"is_done": True, "position": 0})
    assert done.status_code == 200
    assert done.json()["item"]["is_done"] is True and done.json()["item"]["position"] == 0
    assert [i["kind"] for i in done.json()["items"]] == ["item", "thumbnail"]
    assert done.json()["done"] == 1

    note = api(key, "patch", f"{content.pk}/shotlist/{thumbnail['id']}", {"note": "Golden hour", "is_done": True})
    assert note.status_code == 200 and note.json()["done"] == 2

    deleted = api(key, "delete", f"{content.pk}/shotlist/{item['id']}")
    assert deleted.status_code == 200 and deleted.json()["total"] == 1 and deleted.json()["item"] is None
    actions = list(ShotlistChange.objects.filter(post=content).values_list("action", flat=True))
    assert actions == ["added", "done", "done", "deleted"]


def test_thumbnail_rules_validation_and_foreign_items(key, workspace, user, content):
    thumbnail = content.jrc_shotlist_items.get(kind="thumbnail")
    assert api(key, "delete", f"{content.pk}/shotlist/{thumbnail.pk}").status_code == 422
    assert api(key, "patch", f"{content.pk}/shotlist/{thumbnail.pk}", {"text": "Photo"}).status_code == 422
    assert api(key, "post", f"{content.pk}/shotlist", {"text": "   "}).status_code == 422
    assert api(key, "post", f"{content.pk}/shotlist", {"text": "x" * 301}).status_code == 422
    assert api(key, "patch", f"{content.pk}/shotlist/{thumbnail.pk}", {"position": -1}).status_code == 422

    other = create_content(workspace, "Other content", user)
    foreign = other.jrc_shotlist_items.get(kind="thumbnail")
    assert api(key, "patch", f"{content.pk}/shotlist/{foreign.pk}", {"is_done": True}).status_code == 404
    assert api(key, "delete", f"{content.pk}/shotlist/{foreign.pk}").status_code == 404
    foreign.refresh_from_db()
    assert not foreign.is_done
    assert ShotlistItem.objects.filter(kind="thumbnail", post__in=[content, other]).count() == 2


@pytest.mark.parametrize("role", ROLES)
@pytest.mark.parametrize("own", [True, False])
def test_role_matrix_matches_the_tab(workspace, user, org_owner, content, role, own):
    if not own:
        Post.objects.filter(pk=content.pk).update(author=User.objects.create_user(email="author@example.test"))
    WorkspaceMembership.objects.filter(workspace=workspace, user=user).update(workspace_role=role)
    key = make_key(workspace, user)
    can_write = role in ("owner", "manager", "editor") or (role == "contributor" and own)
    thumbnail = content.jrc_shotlist_items.get(kind="thumbnail")

    listing = api(key, "get", f"{content.pk}/shotlist")
    assert listing.status_code == 200 and listing.json()["can_edit"] == can_write
    expected = 201 if can_write else 403
    assert api(key, "post", f"{content.pk}/shotlist", {"text": "Role shot"}).status_code == expected
    expected = 200 if can_write else 403
    assert api(key, "patch", f"{content.pk}/shotlist/{thumbnail.pk}", {"is_done": True}).status_code == expected
    if not can_write:
        thumbnail.refresh_from_db()
        assert not thumbnail.is_done
        assert not ShotlistItem.objects.filter(post=content, text="Role shot").exists()


def test_key_permissions_narrow_the_issuer_and_foreign_workspace(workspace, user, org_owner, content, organization):
    read_only = make_key(workspace, user, permissions=["view_analytics"])
    assert api(read_only, "get", f"{content.pk}/shotlist").status_code == 200
    assert api(read_only, "get", f"{content.pk}/shotlist").json()["can_edit"] is False
    assert api(read_only, "post", f"{content.pk}/shotlist", {"text": "x"}).status_code == 403
    approver = make_key(workspace, user, permissions=["approve_posts"])
    assert api(approver, "post", f"{content.pk}/shotlist", {"text": "x"}).status_code == 403
    assert not ShotlistItem.objects.filter(post=content, text="x").exists()

    elsewhere = Workspace.objects.create(organization=organization, name="Elsewhere")
    WorkspaceMembership.objects.create(workspace=elsewhere, user=user, workspace_role="owner")
    foreign = create_content(elsewhere, "Foreign", user)
    key = make_key(workspace, user)
    assert api(key, "get", f"{foreign.pk}/shotlist").status_code == 404
    assert api(key, "post", f"{foreign.pk}/shotlist", {"text": "x"}).status_code == 404
    assert Client().get(f"/api/v1/posts/{content.pk}/shotlist", secure=True).status_code == 401


def test_openapi_documents_the_shotlist_routes(key):
    spec = Client(HTTP_AUTHORIZATION=f"Bearer {key.plaintext_token}").get("/api/v1/openapi.json", secure=True).json()
    paths = spec["paths"]
    assert {"get", "post"} <= set(paths["/api/v1/posts/{post_id}/shotlist"])
    assert {"patch", "delete"} <= set(paths["/api/v1/posts/{post_id}/shotlist/{item_id}"])
    assert all(entry.get("summary") for entry in paths["/api/v1/posts/{post_id}/shotlist"].values())
