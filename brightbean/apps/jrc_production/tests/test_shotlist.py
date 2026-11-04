# JR Content: přidáno 2026-11-04 (blok P1-B09), AGPL-3.0.
"""Shotlist of a content: automatic thumbnail item, editing, history, permissions, board (P1-B09)."""

from importlib import import_module

import pytest
from django.apps import apps as django_apps
from django.core.exceptions import PermissionDenied, ValidationError
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.composer.models import Post
from apps.jrc_channels.services import create_manual_channel
from apps.jrc_production import shotlist
from apps.jrc_production.models import ShotlistChange, ShotlistItem
from apps.jrc_production.services import create_content
from apps.members.models import WorkspaceMembership

pytestmark = pytest.mark.django_db
ROLES = ["owner", "manager", "editor", "contributor", "client", "viewer"]


@pytest.fixture
def content(workspace, user):
    create_manual_channel(workspace, "instagram", "Ella Instagram")
    return create_content(workspace, "Studio reel", user)


def _member(workspace, role, email):
    member = User.objects.create_user(email=email, password="testpass123", name=role, tos_accepted_at=timezone.now())
    WorkspaceMembership.objects.create(user=member, workspace=workspace, workspace_role=role)
    return member


def _detail(workspace, post, tab="shotlist"):
    url = reverse("jrc_production:content_detail", kwargs={"workspace_id": workspace.id, "post_id": post.id})
    return f"{url}?tab={tab}"


def _action(workspace, post):
    return reverse("jrc_production:shotlist_action", kwargs={"workspace_id": workspace.id, "post_id": post.id})


# ---------------------------------------------------------------------------
# AK3 automatic thumbnail item
# ---------------------------------------------------------------------------


def test_every_new_content_gets_one_thumbnail_item(workspace, user, content):
    items = list(content.jrc_shotlist_items.all())
    assert [(item.kind, item.text, item.is_done) for item in items] == [("thumbnail", "Thumbnail photo", False)]
    # Upstream paths (composer, REST, duplicate) create Post rows directly: the signal covers them too.
    other = Post.objects.create(workspace=workspace, author=user, title="Composer post")
    assert other.jrc_shotlist_items.filter(kind="thumbnail").count() == 1
    other.title = "Renamed"
    other.save()
    assert other.jrc_shotlist_items.count() == 1


def test_data_migration_adds_missing_thumbnail_items_once(workspace, user, content):
    old = Post.objects.create(workspace=workspace, author=user, title="Before P1-B09")
    ShotlistItem.objects.filter(post=old).delete()
    migration = import_module("apps.jrc_production.migrations.0004_shotlist_thumbnail_items")

    migration.add_thumbnail_items(django_apps, None)
    migration.add_thumbnail_items(django_apps, None)

    assert ShotlistItem.objects.filter(post=old, kind="thumbnail", text="Thumbnail photo").count() == 1
    assert ShotlistItem.objects.filter(post=content, kind="thumbnail").count() == 1


def test_thumbnail_item_is_checked_by_hand_and_cannot_be_deleted_or_renamed(workspace, user, content):
    thumbnail = content.jrc_shotlist_items.get(kind="thumbnail")
    with pytest.raises(ValidationError):
        shotlist.delete_item(content, thumbnail.pk, user)
    with pytest.raises(ValidationError):
        shotlist.update_item(content, thumbnail.pk, user, text="Other photo")

    shotlist.update_item(content, thumbnail.pk, user, note="Golden hour", is_done=True)

    thumbnail.refresh_from_db()
    assert thumbnail.is_done and thumbnail.note == "Golden hour" and thumbnail.updated_by == user
    assert ShotlistItem.objects.filter(post=content, kind="thumbnail").count() == 1


# ---------------------------------------------------------------------------
# AK1 / AK2 services
# ---------------------------------------------------------------------------


def test_add_edit_check_move_delete_and_history(workspace, user, content):
    first = shotlist.add_item(content, user, "  Wide shot   of the studio ", "Morning light")
    second = shotlist.add_item(content, user, "Hands on clay")
    assert first.text == "Wide shot of the studio" and first.note == "Morning light"
    assert [i.text for i in shotlist.items_for(content)] == ["Thumbnail photo", first.text, second.text]

    shotlist.update_item(content, first.pk, user, text="Wide shot", note="")
    shotlist.update_item(content, second.pk, user, is_done=True)
    shotlist.update_item(content, second.pk, user, is_done=True)  # unchanged: no second history row
    shotlist.update_item(content, second.pk, user, is_done=False)
    shotlist.move_item(content, second.pk, user, offset=-1)
    shotlist.move_item(content, second.pk, user, offset=-5)  # clamped to the top
    assert [i.text for i in shotlist.items_for(content)] == ["Hands on clay", "Thumbnail photo", "Wide shot"]
    assert [i.position for i in shotlist.items_for(content)] == [0, 1, 2]
    shotlist.move_item(content, second.pk, user, index=99)
    assert [i.text for i in shotlist.items_for(content)][-1] == "Hands on clay"

    shotlist.delete_item(content, first.pk, user)

    assert not ShotlistItem.objects.filter(pk=first.pk).exists()
    actions = list(ShotlistChange.objects.filter(post=content).values_list("action", "item_text", "user"))
    assert actions == [
        ("added", "Wide shot of the studio", user.pk),
        ("added", "Hands on clay", user.pk),
        ("done", "Hands on clay", user.pk),
        ("undone", "Hands on clay", user.pk),
        ("deleted", "Wide shot", user.pk),
    ]
    assert shotlist.progress(shotlist.items_for(content)) == (0, 2)


def test_validation_and_items_of_other_content(workspace, user, content):
    with pytest.raises(ValidationError):
        shotlist.add_item(content, user, "   ")
    with pytest.raises(ValidationError):
        shotlist.add_item(content, user, "x" * 301)
    with pytest.raises(ValidationError):
        shotlist.add_item(content, user, "Shot", "n" * 1001)
    other = create_content(workspace, "Other", user)
    foreign = shotlist.add_item(other, user, "Not here")
    with pytest.raises(shotlist.ItemNotFoundError):
        shotlist.update_item(content, foreign.pk, user, is_done=True)
    with pytest.raises(shotlist.ItemNotFoundError):
        shotlist.delete_item(content, "not-a-uuid", user)
    assert not ShotlistItem.objects.get(pk=foreign.pk).is_done


# ---------------------------------------------------------------------------
# AK4 permissions
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("role", ROLES)
@pytest.mark.parametrize("own", [True, False])
def test_role_matrix_for_services_and_tab(client, workspace, user, role, own):
    create_manual_channel(workspace, "instagram", "IG")
    member = _member(workspace, role, f"{role}@example.test")
    if role in ("viewer",) or not own:
        post = create_content(workspace, "Team content", user)
    else:
        post = create_content(workspace, "Own content", member)
    can_write = role in ("owner", "manager", "editor") or (role == "contributor" and own)

    client.force_login(member)
    page = client.get(_detail(workspace, post))
    assert page.status_code == 200
    assert b"Thumbnail photo" in page.content
    assert (b'name="action" value="add"' in page.content) == can_write

    response = client.post(_action(workspace, post), {"action": "add", "text": "Role shot"})
    if can_write:
        assert response.status_code == 302
        assert ShotlistItem.objects.filter(post=post, text="Role shot").exists()
    else:
        assert response.status_code == 403
        assert not ShotlistItem.objects.filter(post=post, text="Role shot").exists()
        thumbnail = post.jrc_shotlist_items.get(kind="thumbnail")
        done = client.post(_action(workspace, post), {"action": "done", "item": thumbnail.pk, "done": "1"})
        assert done.status_code == 403
        with pytest.raises(PermissionDenied):
            shotlist.update_item(post, thumbnail.pk, member, is_done=True)
        thumbnail.refresh_from_db()
        assert not thumbnail.is_done


def test_non_member_gets_404(client, workspace, user, content):
    stranger = User.objects.create_user(email="stranger@example.test", password="x", tos_accepted_at=timezone.now())
    client.force_login(stranger)
    assert client.get(_detail(workspace, content)).status_code == 404
    assert client.post(_action(workspace, content), {"action": "add", "text": "x"}).status_code == 404
    assert not ShotlistItem.objects.filter(post=content, text="x").exists()


# ---------------------------------------------------------------------------
# AK2 tab, fetch responses, history and board
# ---------------------------------------------------------------------------


def test_tab_flow_with_and_without_script(client, workspace, user, content):
    client.force_login(user)
    url = _action(workspace, content)

    plain = client.post(url, {"action": "add", "text": "B-roll of the kiln", "note": "Slow motion"})
    assert plain.status_code == 302
    assert plain["Location"].endswith("?tab=shotlist&focus=add")
    page = client.get(plain["Location"])
    assert b"B-roll of the kiln" in page.content and b"Slow motion" in page.content
    assert b"0/2 done" in page.content and b"autofocus" in page.content
    assert b'href="?tab=shotlist"' in page.content

    item = content.jrc_shotlist_items.get(text="B-roll of the kiln")
    fragment = client.post(url, {"action": "done", "item": item.pk, "done": "1"}, HTTP_X_JRC_SHOTLIST="1")
    assert fragment.status_code == 200
    assert b"1/2 done" in fragment.content and b"<html" not in fragment.content
    assert b'aria-checked="true"' in fragment.content

    error = client.post(url, {"action": "add", "text": "  "}, HTTP_X_JRC_SHOTLIST="1")
    assert error.status_code == 400 and b'role="alert"' in error.content

    item_id = item.pk
    item.delete()
    gone = client.post(url, {"action": "up", "item": item_id}, HTTP_X_JRC_SHOTLIST="1")
    assert gone.status_code == 409 and b"no longer exists" in gone.content

    thumbnail = content.jrc_shotlist_items.get(kind="thumbnail")
    kept = client.post(url, {"action": "delete", "item": thumbnail.pk}, HTTP_X_JRC_SHOTLIST="1")
    assert kept.status_code == 400 and b"cannot be deleted" in kept.content
    assert client.post(url, {"action": "nope"}).status_code == 400


def test_history_shows_shotlist_changes_to_team_and_client(client, workspace, user, content):
    item = shotlist.add_item(content, user, "Portrait in the garden")
    shotlist.update_item(content, item.pk, user, is_done=True)
    shotlist.delete_item(content, item.pk, user)
    ella = _member(workspace, "client", "ella@example.test")

    for member in (user, ella):
        client.force_login(member)
        page = client.get(_detail(workspace, content, "history")).content.decode()
        assert "Added &quot;Portrait in the garden&quot; to the shotlist" in page
        assert "Checked off &quot;Portrait in the garden&quot; in the shotlist" in page
        assert "Removed &quot;Portrait in the garden&quot; from the shotlist" in page


def test_board_card_shows_shotlist_progress(client, workspace, user, content):
    item = shotlist.add_item(content, user, "Shot one")
    shotlist.add_item(content, user, "Shot two")
    shotlist.update_item(content, item.pk, user, is_done=True)
    client.force_login(user)

    page = client.get(reverse("jrc_production:board"))

    assert b"Shotlist 1/3 done" in page.content
