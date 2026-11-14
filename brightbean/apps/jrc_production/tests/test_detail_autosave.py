"""P2-B08: detail without page loads – tab fragments, automatic saving with the version rule, channel tags."""

import importlib
import json
from datetime import timedelta

import pytest
from django.apps import apps as django_apps
from django.db import IntegrityError, connection, transaction
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.approvals.models import ApprovalAction
from apps.composer.models import PlatformPost, Post
from apps.jrc_channels.services import create_manual_channel
from apps.jrc_production import shotlist as shotlist_services
from apps.jrc_production.services import create_content
from apps.jrc_texts import services as text_services
from apps.jrc_texts.models import TextDocument, TextVersion
from apps.members.models import WorkspaceMembership

pytestmark = pytest.mark.django_db
DEVICE = "device-aaaa-1111"
OTHER_DEVICE = "device-bbbb-2222"


def doc(text):
    paragraph = {"type": "paragraph", "content": [{"type": "text", "text": text}]} if text else {"type": "paragraph"}
    return {"type": "doc", "content": [paragraph]}


def later(monkeypatch, minutes):
    """The clock of the services moves on (saved versions themselves are never rewritten in tests)."""
    moved = timezone.now() + timedelta(minutes=minutes)
    monkeypatch.setattr("django.utils.timezone.now", lambda: moved)


@pytest.fixture
def channel(workspace):
    return create_manual_channel(workspace, "instagram", "Ella Instagram")


@pytest.fixture
def post(workspace, user, channel):
    return create_content(workspace, "Autosave reel", user, social_account_ids=[channel.pk])


@pytest.fixture
def variant(post):
    return post.platform_posts.get()


@pytest.fixture
def editor(workspace):
    person = User.objects.create_user(
        email="editor@p2-b08.example.invalid", password="x", name="Eda Editor", tos_accepted_at=timezone.now()
    )
    WorkspaceMembership.objects.create(workspace=workspace, user=person, workspace_role="editor")
    return person


def member(workspace, role):
    person = User.objects.create_user(
        email=f"{role}@p2-b08.example.invalid", password="x", tos_accepted_at=timezone.now()
    )
    WorkspaceMembership.objects.create(workspace=workspace, user=person, workspace_role=role)
    return person


def save(post, user, text, *, base=None, revision=0, device=DEVICE, kind="concept"):
    return text_services.autosave_text(
        post.pk, user, kind=kind, content=doc(text), base_version_id=base, revision=revision, device=device
    )


def finals(post, kind="concept"):
    return list(TextVersion.objects.filter(document__post=post, document__kind=kind, state="final").order_by("number"))


# ----- the version rule (AK2) -----


def test_first_edit_creates_a_version_and_further_edits_write_into_it(post, user):
    first = save(post, user, "Idea one")
    assert first.created and first.changed and first.version.number == 1
    second = save(post, user, "Idea one, better", base=first.version.pk, revision=first.version.revision)
    assert not second.created and second.changed
    assert second.version.pk == first.version.pk and second.version.revision == first.version.revision + 1
    third = save(post, user, "Idea one, best", base=second.version.pk, revision=second.version.revision)
    assert [v.text for v in finals(post)] == ["Idea one, best"]
    assert third.version.finalized_at >= first.version.finalized_at


def test_a_concept_with_text_from_creation_gets_a_new_version_on_the_first_edit(workspace, user, channel):
    created = create_content(workspace, "With description", user, description="Typed while creating")
    v1 = finals(created)[0]
    result = save(created, user, "Edited in the detail", base=v1.pk, revision=v1.revision)
    assert result.created and result.version.number == 2
    assert [v.text for v in finals(created)] == ["Typed while creating", "Edited in the detail"]


def test_after_fifteen_minutes_without_an_edit_a_new_version_starts(post, user, monkeypatch):
    first = save(post, user, "Morning text")
    later(monkeypatch, 15.1)
    afternoon = save(post, user, "Morning text, afternoon edit", base=first.version.pk, revision=first.version.revision)
    assert afternoon.created and afternoon.version.number == 2
    assert [v.text for v in finals(post)] == ["Morning text", "Morning text, afternoon edit"]


def test_within_fifteen_minutes_of_the_last_edit_the_version_stays_open(post, user, monkeypatch):
    first = save(post, user, "Start")
    later(monkeypatch, 14)
    again = save(post, user, "Start, continued", base=first.version.pk, revision=first.version.revision)
    assert not again.created and len(finals(post)) == 1


def test_another_device_of_the_same_person_starts_its_own_version(post, user):
    first = save(post, user, "Laptop text")
    phone = save(post, user, "Laptop text, phone edit", base=first.version.pk, revision=0, device=OTHER_DEVICE)
    assert phone.created and phone.version.number == 2
    assert finals(post)[0].text == "Laptop text"


def test_another_person_never_writes_into_somebody_elses_version(post, user, editor):
    mine = save(post, user, "Owner text")
    theirs = save(post, editor, "Owner text, editor change", base=mine.version.pk, revision=mine.version.revision)
    assert theirs.created and theirs.version.created_by == editor
    assert finals(post)[0].text == "Owner text" and finals(post)[0].created_by == user


def test_simultaneous_editing_keeps_each_persons_version_and_reports_the_other(post, user, editor):
    base = save(post, user, "Shared start", device=OTHER_DEVICE)
    owner_edit = save(post, user, "Owner edit", base=base.version.pk, revision=base.version.revision)
    # The editor started from the same version before the owner's edit arrived.
    editor_edit = save(post, editor, "Editor edit", base=base.version.pk, revision=base.version.revision)
    assert editor_edit.created and editor_edit.concurrent.pk == owner_edit.version.pk
    # The owner keeps typing: the editor's newer version is never overwritten, the owner gets a new one.
    owner_again = save(post, user, "Owner edit, more", base=owner_edit.version.pk, revision=owner_edit.version.revision)
    assert owner_again.created and owner_again.concurrent.pk == editor_edit.version.pk
    assert [(v.number, v.text) for v in finals(post)] == [
        (1, "Shared start"),
        (2, "Owner edit"),
        (3, "Editor edit"),
        (4, "Owner edit, more"),
    ]


def test_a_stale_tab_never_overwrites_the_text_another_tab_wrote(post, user):
    first = save(post, user, "Tab A")
    tab_a = save(post, user, "Tab A, more", base=first.version.pk, revision=first.version.revision)
    tab_b = save(post, user, "Tab B text", base=first.version.pk, revision=first.version.revision)
    assert tab_b.created
    assert [v.text for v in finals(post)] == ["Tab A, more", "Tab B text"]
    assert tab_a.version.number == 1


def test_an_edit_without_a_change_creates_no_version(post, user):
    assert save(post, user, "").version is None
    assert not TextDocument.objects.filter(post=post, kind="concept").exists()
    first = save(post, user, "Same")
    unchanged = save(post, user, "Same", base=first.version.pk, revision=first.version.revision)
    assert not unchanged.changed and not unchanged.created
    assert len(finals(post)) == 1


def test_an_approval_decision_closes_the_open_version(post, user):
    first = save(post, user, "Text the reviewer saw")
    ApprovalAction.objects.create(post=post, user=user, action=ApprovalAction.ActionType.APPROVED)
    after = save(post, user, "Changed after approval", base=first.version.pk, revision=first.version.revision)
    assert after.created
    assert finals(post)[0].text == "Text the reviewer saw"


def test_only_the_new_version_notifies_the_team(post, user, editor, monkeypatch):
    sent = []
    monkeypatch.setattr(text_services, "notify_text_version", lambda version, actor: sent.append(version.number))
    first = save(post, user, "One")
    save(post, user, "One two", base=first.version.pk, revision=first.version.revision)
    save(post, editor, "Editor", base=first.version.pk, revision=first.version.revision + 1)
    assert sent == [1, 2]


@pytest.mark.parametrize("device", ["", "short", "x" * 65, "bad device!", None, 5])
def test_the_device_must_be_a_valid_browser_id(post, user, device):
    with pytest.raises(text_services.ValidationError):
        save(post, user, "Text", device=device)


def test_reader_roles_cannot_autosave(post, workspace):
    for role in ("client", "viewer"):
        with pytest.raises(text_services.PermissionDenied):
            save(post, member(workspace, role), "Not allowed")
    assert not TextVersion.objects.filter(document__post=post).exists()


def _check_now():
    with connection.cursor() as cursor:
        cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")


def _stored(post, user, *, device=DEVICE, edited_minutes_ago=0, number=1):
    document, _ = TextDocument.objects.get_or_create(post=post, kind="script", defaults={"workspace": post.workspace})
    edited = timezone.now() - timedelta(minutes=edited_minutes_ago)
    return TextVersion.objects.create(
        document=document,
        number=number,
        state="final",
        content=doc("Stored"),
        text="Stored",
        created_by=user,
        finalized_at=edited,
        edited_at=edited,
        device=device,
    )


@pytest.mark.parametrize(
    ("device", "minutes", "change"),
    [
        (DEVICE, 30, {}),  # the window has passed
        ("", 0, {}),  # saved without automatic saving: never open
        (DEVICE, 0, {"number": 99}),  # inside the window only text, revision and times may change
        (DEVICE, 0, {"created_by": None}),
    ],
)
def test_the_database_keeps_saved_versions_unchangeable_outside_the_rule(post, user, device, minutes, change):
    version = _stored(post, user, device=device, edited_minutes_ago=minutes)
    with pytest.raises(IntegrityError), transaction.atomic():
        TextVersion.objects.filter(pk=version.pk).update(
            text="Tampered", revision=version.revision + 1, edited_at=timezone.now(), **change
        )
        _check_now()
    assert TextVersion.objects.get(pk=version.pk).text == "Stored"


def test_the_database_allows_the_author_device_to_write_within_the_window(post, user):
    version = _stored(post, user, edited_minutes_ago=10)
    with transaction.atomic():
        TextVersion.objects.filter(pk=version.pk).update(
            text="Amended", revision=version.revision + 1, edited_at=timezone.now(), finalized_at=timezone.now()
        )
        _check_now()
    assert TextVersion.objects.get(pk=version.pk).text == "Amended"


# ----- captions -----


def caption(post, variant, user, text, *, base=None, revision=0, device=DEVICE):
    return text_services.autosave_caption(
        post.pk, variant.pk, user, text, base_version_id=base, revision=revision, device=device
    )


def test_caption_autosave_follows_the_version_rule_and_the_copy_follows_the_newest_text(post, variant, user):
    first = caption(post, variant, user, "Hello")
    again = caption(post, variant, user, "Hello world", base=first.version.pk, revision=first.version.revision)
    assert first.created and not again.created and again.version.pk == first.version.pk
    variant.refresh_from_db()
    assert variant.platform_specific_caption == "Hello world"
    assert [v.text for v in finals(post, "caption")] == ["Hello world"]


def test_caption_history_starts_from_the_caption_the_copy_already_had(post, variant, user):
    PlatformPost.objects.filter(pk=variant.pk).update(platform_specific_caption="Imported caption")
    variant.refresh_from_db()
    result = caption(post, variant, user, "Imported caption, edited")
    assert [v.text for v in finals(post, "caption")] == ["Imported caption", "Imported caption, edited"]
    assert result.version.number == 2


def test_caption_over_the_limit_is_refused_and_nothing_is_stored(post, variant, user):
    with pytest.raises(text_services.ValidationError):
        caption(post, variant, user, "x" * 2201)
    assert not TextDocument.objects.filter(platform_post=variant).exists()


def test_caption_change_returns_approved_copy_to_review_also_inside_an_open_version(workspace, post, variant, user):
    workspace.approval_workflow_mode = "required_internal"
    workspace.save(update_fields=["approval_workflow_mode"])
    first = caption(post, variant, user, "Approved text")
    PlatformPost.objects.filter(pk=variant.pk).update(status="approved")
    edited = caption(
        post, variant, user, "Approved text, changed", base=first.version.pk, revision=first.version.revision
    )
    assert [pp.pk for pp in edited.returned] == [variant.pk]
    variant.refresh_from_db()
    assert variant.status == "pending_review"


def test_caption_endpoint_answers_with_label_versions_and_notice(client, workspace, post, variant, user):
    client.force_login(user)
    url = reverse(
        "jrc_production:caption_autosave",
        kwargs={"workspace_id": workspace.pk, "post_id": post.pk, "platform_post_id": variant.pk},
    )
    response = client.post(url, {"text": "From the browser", "device": DEVICE}, content_type="application/json")
    assert response.status_code == 200
    data = response.json()
    assert data["created"] and data["version"]["number"] == 1 and data["version"]["label"].startswith("V1 · ")
    assert "Saved versions (1)" in data["versions_html"]
    again = client.post(
        url,
        {
            "text": "From the browser!",
            "base": data["version"]["id"],
            "revision": data["version"]["revision"],
            "device": DEVICE,
        },
        content_type="application/json",
    ).json()
    assert not again["created"] and again["version"]["number"] == 1
    over = client.post(url, {"text": "x" * 3000, "device": DEVICE}, content_type="application/json")
    assert over.status_code == 400 and "limit" in over.json()["error"]
    foreign = client.post(
        url, {"text": "x", "base": str(variant.pk), "device": DEVICE}, content_type="application/json"
    )
    assert foreign.status_code == 409
    for role in ("client", "viewer"):
        client.force_login(member(workspace, role))
        assert client.post(url, {"text": "No", "device": DEVICE}, content_type="application/json").status_code == 403
    assert finals(post, "caption")[0].text == "From the browser!"


# ----- Script/Concept endpoint -----


def test_text_endpoint_autosaves_and_refuses_the_old_draft_actions(client, workspace, post, user):
    client.force_login(user)
    url = reverse("jrc_texts:concept", kwargs={"workspace_id": workspace.pk, "post_id": post.pk})
    data = {"action": "autosave", "base": None, "device": DEVICE, "content": doc("Typed")}
    first = client.post(url, data, content_type="application/json").json()
    assert first["saved"]["created"] and first["latest"] == first["saved"]["version"]["id"]
    data.update(base=first["latest"], revision=first["saved"]["version"]["revision"], content=doc("Typed more"))
    second = client.post(url, data, content_type="application/json").json()
    assert not second["saved"]["created"] and len(second["versions"]) == 1
    for action in ("edit", "save", "discard"):
        response = client.post(url, {**data, "action": action}, content_type="application/json")
        assert response.status_code == 400
    bad = client.post(url, {**data, "device": "x"}, content_type="application/json")
    assert bad.status_code == 400
    other = create_content(workspace, "Other", user)
    other_url = reverse("jrc_texts:concept", kwargs={"workspace_id": workspace.pk, "post_id": other.pk})
    assert client.post(other_url, data, content_type="application/json").status_code == 409
    client.force_login(member(workspace, "viewer"))
    assert client.post(url, data, content_type="application/json").status_code == 403


def test_old_editor_drafts_become_saved_versions(post, user):
    first = save(post, user, "Saved before")
    draft = text_services.edit(first.version.pk, user)
    text_services.update_draft(draft.pk, user, expected_revision=0, content=doc("Typed in the old editor"))
    script_document = text_services.create_document(post.pk, user, kind="script", markdown="", initial_draft=True)
    module = importlib.import_module("apps.jrc_texts.migrations.0006_autosave_versions")
    module.drafts_to_versions(django_apps, None)
    assert [v.text for v in finals(post)] == ["Saved before", "Typed in the old editor"]
    assert not TextVersion.objects.filter(state="draft").exists()
    # An empty first draft held nothing: it is removed, not saved.
    assert not script_document.versions.exists()


# ----- tabs as fragments (AK1) -----


def test_tab_fragment_is_only_the_panel_and_never_cached_as_the_page(client, workspace, post, user):
    client.force_login(user)
    url = reverse("jrc_production:content_detail", kwargs={"workspace_id": workspace.pk, "post_id": post.pk})
    for tab in ("concept", "script", "shotlist", "video", "caption", "approval", "discussion", "publish", "history"):
        response = client.get(url, {"tab": tab}, HTTP_X_JRC_TAB="1")
        assert response.status_code == 200
        body = response.content.decode()
        assert "<html" not in body and f'data-tab="{tab}"' in body
        assert response["X-JRC-Tab"] == tab
        assert "X-JRC-Tab" in response["Vary"] and "no-store" in response["Cache-Control"]
    page = client.get(url, {"tab": "caption"})
    body = page.content.decode()
    assert "<html" in body and 'data-tab="caption"' in body and "data-copy-tab-link" in body
    assert "X-JRC-Tab" in page["Vary"] and "X-JRC-Tab" not in page
    # Unknown tabs fall back like a page load; the old "comments" address stays valid.
    assert client.get(url, {"tab": "nope"}, HTTP_X_JRC_TAB="1")["X-JRC-Tab"] == "concept"
    assert client.get(url, {"tab": "comments"}, HTTP_X_JRC_TAB="1")["X-JRC-Tab"] == "discussion"


def test_tab_fragment_has_the_same_access_rules_as_the_page(client, workspace, post, user):
    url = reverse("jrc_production:content_detail", kwargs={"workspace_id": workspace.pk, "post_id": post.pk})
    outsider = User.objects.create_user(
        email="out@p2-b08.example.invalid", password="x", tos_accepted_at=timezone.now()
    )
    client.force_login(outsider)
    assert client.get(url, {"tab": "caption"}, HTTP_X_JRC_TAB="1").status_code == 404
    client.force_login(member(workspace, "viewer"))
    body = client.get(url, {"tab": "caption"}, HTTP_X_JRC_TAB="1").content.decode()
    assert "data-autosave-url" not in body and "readonly" in body
    Client().logout()


def test_tab_links_have_their_own_addresses_and_panel_scripts_come_with_the_fragment(client, workspace, post, user):
    client.force_login(user)
    url = reverse("jrc_production:content_detail", kwargs={"workspace_id": workspace.pk, "post_id": post.pk})
    page = client.get(url, {"tab": "shotlist"}).content.decode()
    for tab in ("concept", "script", "shotlist", "caption", "history"):
        assert f'href="?tab={tab}"' in page and f'id="content-tab-{tab}"' in page
    assert "jrc_production/autosave.js" in page and "jrc_production/channel-tags.js" in page
    assert "jrc_production/shotlist.js" in client.get(url, {"tab": "shotlist"}, HTTP_X_JRC_TAB="1").content.decode()
    assert "caption-autosave.js" in client.get(url, {"tab": "caption"}, HTTP_X_JRC_TAB="1").content.decode()
    assert "jrc_texts/dist/script.js" in client.get(url, {"tab": "script"}, HTTP_X_JRC_TAB="1").content.decode()


# ----- shotlist edited in place -----


def test_shotlist_item_saves_in_place_with_json(client, workspace, post, user):
    client.force_login(user)
    item = shotlist_services.add_item(post, user, "Wide shot", "")
    url = reverse("jrc_production:shotlist_action", kwargs={"workspace_id": workspace.pk, "post_id": post.pk})
    headers = {"HTTP_X_JRC_SHOTLIST_AUTOSAVE": "1"}
    response = client.post(
        url, {"action": "update", "item": item.pk, "text": "Wide shot, sunset", "note": "Tripod"}, **headers
    )
    assert response.status_code == 200
    assert response.json()["text"] == "Wide shot, sunset" and response.json()["meta"].startswith("Updated by")
    item.refresh_from_db()
    assert (item.text, item.note) == ("Wide shot, sunset", "Tripod")
    assert (
        client.post(url, {"action": "update", "item": item.pk, "text": " ", "note": ""}, **headers).status_code == 400
    )
    client.force_login(member(workspace, "viewer"))
    assert client.post(url, {"action": "update", "item": item.pk, "text": "No", "note": ""}, **headers).status_code in (
        403,
        404,
    )
    item.refresh_from_db()
    assert item.text == "Wide shot, sunset"


# ----- channel tags (AK3) -----


def channels_url(workspace, post):
    return reverse("jrc_production:channels", kwargs={"workspace_id": workspace.pk, "post_id": post.pk})


def test_channel_tags_add_and_remove_without_a_page_load(client, workspace, user, channel):
    client.force_login(user)
    post = create_content(workspace, "No channel yet", user)
    added = client.post(
        channels_url(workspace, post), {"action": "add_channel", "channel": channel.pk, "tab": "caption"}
    )
    assert added.status_code == 200 and added.json()["message"] == "Channel added."
    assert "Remove channel Ella Instagram" in added.json()["html"]
    variant = post.platform_posts.get()
    removed = client.post(channels_url(workspace, post), {"action": "remove_channel", "platform_post": variant.pk})
    assert removed.status_code == 200 and "No channels" in removed.json()["html"]
    assert not post.platform_posts.exists()


def test_removing_a_channel_with_work_is_refused_and_keeps_everything(client, workspace, post, variant, user):
    client.force_login(user)
    caption(post, variant, user, "Work that must stay")
    response = client.post(channels_url(workspace, post), {"action": "remove_channel", "platform_post": variant.pk})
    assert response.status_code == 400 and "cannot be removed" in response.json()["error"]
    assert PlatformPost.objects.filter(pk=variant.pk).exists()
    assert finals(post, "caption")[0].text == "Work that must stay"


def test_channel_tags_follow_the_editing_rule_and_roles(client, workspace, post, variant, user):
    PlatformPost.objects.filter(pk=variant.pk).update(status="pending_review")
    client.force_login(user)
    url = reverse("jrc_production:content_detail", kwargs={"workspace_id": workspace.pk, "post_id": post.pk})
    page = client.get(url).content.decode()
    assert "Move the content back to Editing to change its channels." in page
    assert "channel-tag-remove" not in page
    refused = client.post(channels_url(workspace, post), {"action": "remove_channel", "platform_post": variant.pk})
    assert refused.status_code == 400 and PlatformPost.objects.filter(pk=variant.pk).exists()
    for role in ("client", "viewer"):
        client.force_login(member(workspace, role))
        response = client.post(channels_url(workspace, post), {"action": "remove_channel", "platform_post": variant.pk})
        assert response.status_code == 403
        assert "channel-tag-remove" not in client.get(url).content.decode()
    assert json.loads(refused.content)["html"]
    assert Post.objects.filter(pk=post.pk).exists()
