# JR Content: přidáno 2026-11-07 (blok P1-B36), AGPL-3.0.
"""Edits after approval on every path, CSV/TSV reading, taking content out of the plan (P1-B36).

Expectations are written by hand, not derived from ``apps.members.permissions``:

* PM decision P-025 A: in the modes that require approval ("Internal review required" and the
  two-stage mode) a changed caption, title, first comment, tags or media of an approved channel copy
  sends it back to review on every path — JR detail Caption tab, REST caption, MCP
  ``jrc_save_caption``, REST ``PATCH /posts/{id}``, composer. Saving the same text keeps the approval,
  another channel's own caption is not touched, "No approvals" and "Optional approvals" keep it.
* CSV import reads comma, semicolon and tab files (``.tsv`` always tab), UTF-8 with or without BOM and
  UTF-16 with BOM; any other encoding gets a message instead of a server error.
* Taking planned content out of the plan (calendar bulk Draft, composer chip, queue, JR board, REST
  ``/cancel``, MCP ``cancel_post``) needs the planning right plus edit rights for that content: owner
  and manager may, editor and contributor may not.
"""

import json
from datetime import timedelta

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.utils import timezone

from apps.accounts.models import User
from apps.api_keys import services as key_services
from apps.composer.models import PlatformPost, Post, PostMedia
from apps.jrc_channels.services import create_manual_channel
from apps.media_library.models import MediaAsset
from apps.members.models import PERMISSION_KEYS, OrgMembership, WorkspaceMembership
from apps.workspaces.models import Workspace

pytestmark = pytest.mark.django_db

MODES = ["none", "optional", "required_internal", "required_internal_and_client"]
RETURNS = {"none": False, "optional": False, "required_internal": True, "required_internal_and_client": True}


class _SecureClient(Client):
    def generic(self, method, path, *args, **kwargs):
        kwargs["secure"] = True
        return super().generic(method, path, *args, **kwargs)


@pytest.fixture
def make(organization):
    counter = {"n": 0}

    def build(mode="required_internal_and_client", role="owner"):
        counter["n"] += 1
        n = counter["n"]
        workspace = Workspace.objects.create(
            organization=organization, name=f"B36 {mode} {role} {n}", approval_workflow_mode=mode
        )
        account = create_manual_channel(workspace, "custom", f"B36 channel {n}")
        second = create_manual_channel(workspace, "custom", f"B36 second {n}")
        actor = User.objects.create_user(
            email=f"b36-{role}-{n}@example.invalid", tos_accepted_at=timezone.now(), name=f"B36 {role}"
        )
        OrgMembership.objects.create(
            user=actor, organization=organization, org_role="owner" if role == "owner" else "member"
        )
        WorkspaceMembership.objects.create(user=actor, workspace=workspace, workspace_role=role)
        client = Client()
        client.force_login(actor)
        return workspace, account, second, actor, client

    return build


def _content(workspace, accounts, author, status="approved", **copy_fields):
    post = Post.objects.create(
        workspace=workspace, author=author, title="B36 title", caption="Base caption", first_comment="First"
    )
    copies = [
        PlatformPost.objects.create(post=post, social_account=account, status=status, **copy_fields)
        for account in accounts
    ]
    return post, copies


def _statuses(*copies):
    for copy in copies:
        copy.refresh_from_db()
    return [copy.status for copy in copies]


def _api(workspace, accounts, user):
    """A key acting as ``user`` (its issuer), so it carries exactly ``user``'s workspace rights."""
    issuer = User.objects.create_user(email=f"b36-issuer-{user.pk}@example.invalid", tos_accepted_at=timezone.now())
    OrgMembership.objects.create(user=issuer, organization=workspace.organization, org_role="owner")
    WorkspaceMembership.objects.create(user=issuer, workspace=workspace, workspace_role="owner")
    key = key_services.issue_api_key(
        workspace=workspace,
        social_accounts=list(accounts),
        issued_by=issuer,
        name=f"b36-{user.pk}",
        permissions=list(PERMISSION_KEYS),
    )
    key.api_key.issued_by = user
    key.api_key.save(update_fields=["issued_by"])
    return _SecureClient(HTTP_AUTHORIZATION=f"Bearer {key.plaintext_token}")


def _mcp(client, name, arguments):
    response = client.post(
        "/api/v1/mcp/",
        data=json.dumps(
            {"jsonrpc": "2.0", "method": "tools/call", "params": {"name": name, "arguments": arguments}, "id": 1}
        ),
        content_type="application/json",
    )
    assert response.status_code == 200
    return response.json()


# ------------------------------------------------------------------------- AK1 edit after approval


def _save_in_detail(client, workspace, post, copy, text):
    return client.post(
        f"/workspace/{workspace.pk}/content/{post.pk}/?tab=caption",
        {"action": "save_caption", "platform_post": str(copy.pk), "caption": text},
    )


def _save_via_rest(workspace, post, copy, actor, text):
    client = _api(workspace, [pp.social_account for pp in post.platform_posts.all()], actor)
    return client.post(
        f"/api/v1/posts/{post.pk}/captions/{copy.pk}", data=json.dumps({"text": text}), content_type="application/json"
    )


def _save_via_mcp(workspace, post, copy, actor, text):
    client = _api(workspace, [pp.social_account for pp in post.platform_posts.all()], actor)
    return _mcp(client, "jrc_save_caption", {"post_id": str(post.pk), "platform_post_id": str(copy.pk), "text": text})


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("path", ["detail", "rest", "mcp"])
def test_caption_version_after_approval_returns_only_that_copy_where_required(make, mode, path):
    workspace, account, second, actor, client = make(mode)
    post, (copy, other) = _content(workspace, [account, second], actor)
    if path == "detail":
        response = _save_in_detail(client, workspace, post, copy, "Fixed typo")
        assert response.status_code == 302
    elif path == "rest":
        assert _save_via_rest(workspace, post, copy, actor, "Fixed typo").status_code == 201
    else:
        assert "error" not in _save_via_mcp(workspace, post, copy, actor, "Fixed typo")
    copy.refresh_from_db()
    assert copy.platform_specific_caption == "Fixed typo"
    assert _statuses(copy, other) == ["pending_review" if RETURNS[mode] else "approved", "approved"]


@pytest.mark.parametrize("path", ["detail", "rest", "mcp"])
def test_saving_the_same_caption_keeps_the_approval(make, path):
    workspace, account, second, actor, client = make()
    post, (copy,) = _content(workspace, [account], actor)
    if path == "detail":
        _save_in_detail(client, workspace, post, copy, "Base caption")
    elif path == "rest":
        assert _save_via_rest(workspace, post, copy, actor, "Base caption").status_code in (200, 201)
    else:
        assert "error" not in _save_via_mcp(workspace, post, copy, actor, "Base caption")
    assert _statuses(copy) == ["approved"]


def test_detail_tells_before_and_after_saving(make):
    workspace, account, second, actor, client = make("required_internal")
    post, (copy,) = _content(workspace, [account], actor)
    page = client.get(f"/workspace/{workspace.pk}/content/{post.pk}/?tab=caption").content.decode()
    assert "saving a changed caption sends it back for re-approval" in page
    response = _save_in_detail(client, workspace, post, copy, "Changed")
    page = client.get(response["Location"]).content.decode()
    # P1-B37: the notice names the channel and what changed.
    assert "goes back to review for approval because the caption changed." in page
    assert "saving a changed caption sends it back for re-approval" not in page


def test_detail_hint_absent_where_edits_keep_approval(make):
    workspace, account, second, actor, client = make("optional")
    post, _ = _content(workspace, [account], actor)
    page = client.get(f"/workspace/{workspace.pk}/content/{post.pk}/?tab=caption").content.decode()
    assert "sends it back for re-approval" not in page


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize(
    "change", [{"title": "New title"}, {"first_comment": "New comment"}, {"media": True}, {"internal_notes": "x"}]
)
def test_rest_patch_after_approval(make, mode, change):
    workspace, account, second, actor, client = make(mode)
    post, (copy, other) = _content(workspace, [account, second], actor)
    payload = dict(change)
    if payload.pop("media", False):
        asset = MediaAsset.objects.create(
            organization=workspace.organization,
            workspace=workspace,
            filename="b36.png",
            media_type="image",
            mime_type="image/png",
            file_size=1,
        )
        payload["media_asset_ids"] = [str(asset.pk)]
    api = _api(workspace, [account, second], actor)
    response = api.patch(f"/api/v1/posts/{post.pk}", data=json.dumps(payload), content_type="application/json")
    assert response.status_code == 200, response.content
    reviewed = "internal_notes" not in change  # team-only notes are not reviewed content
    expected = "pending_review" if RETURNS[mode] and reviewed else "approved"
    assert _statuses(copy, other) == [expected, expected]


def test_rest_patch_with_the_same_values_keeps_the_approval(make):
    workspace, account, second, actor, client = make()
    post, (copy,) = _content(workspace, [account], actor)
    api = _api(workspace, [account], actor)
    response = api.patch(
        f"/api/v1/posts/{post.pk}",
        data=json.dumps({"title": "B36 title", "first_comment": "First"}),
        content_type="application/json",
    )
    assert response.status_code == 200
    assert _statuses(copy) == ["approved"]


def test_base_caption_edit_leaves_a_copy_with_its_own_caption(make):
    """A copy whose own caption does not change publishes the same text, so it keeps its approval."""
    workspace, account, second, actor, client = make("required_internal")
    post, (own,) = _content(workspace, [account], actor, platform_specific_caption="Own caption")
    (inherits,) = [PlatformPost.objects.create(post=post, social_account=second, status="approved")]
    client.post(
        f"/workspace/{workspace.pk}/compose/{post.pk}/save/",
        {
            "action": "save_draft",
            "title": post.title,
            "caption": "Edited base",
            "first_comment": "First",
            "selected_accounts": f"{account.pk},{second.pk}",
            f"override_caption_{account.pk}": "Own caption",
        },
    )
    post.refresh_from_db()
    assert post.caption == "Edited base"
    assert _statuses(own, inherits) == ["approved", "pending_review"]


def test_composer_override_edit_returns_only_that_copy(make):
    workspace, account, second, actor, client = make("required_internal")
    post, (copy, other) = _content(workspace, [account, second], actor)
    client.post(
        f"/workspace/{workspace.pk}/compose/{post.pk}/save/",
        {
            "action": "save_draft",
            "title": post.title,
            "caption": post.caption,
            "first_comment": "First",
            "selected_accounts": f"{account.pk},{second.pk}",
            f"override_comment_{account.pk}": "Own first comment",
        },
    )
    assert _statuses(copy, other) == ["pending_review", "approved"]


def test_composer_media_remove_and_reattach_follow_the_rule(make):
    workspace, account, second, actor, client = make("required_internal")
    post, (copy,) = _content(workspace, [account], actor)
    asset = MediaAsset.objects.create(
        organization=workspace.organization,
        workspace=workspace,
        filename="b36.png",
        media_type="image",
        mime_type="image/png",
        file_size=1,
    )
    attachment = PostMedia.objects.create(post=post, media_asset=asset, position=0)
    # Attaching an asset that is already attached changes nothing.
    client.post(f"/workspace/{workspace.pk}/compose/{post.pk}/attach-media/", {"media_asset_id": str(asset.pk)})
    assert _statuses(copy) == ["approved"]
    client.post(f"/workspace/{workspace.pk}/compose/{post.pk}/remove-media/{attachment.pk}/")
    assert not PostMedia.objects.filter(pk=attachment.pk).exists()
    assert _statuses(copy) == ["pending_review"]


def test_script_edit_after_approval_keeps_it(make):
    """The script is a production text that never goes out; it is not reviewed content."""
    from apps.jrc_texts import services as text_services

    workspace, account, second, actor, client = make("required_internal")
    post, (copy,) = _content(workspace, [account], actor)
    text_services.create_document(post.pk, actor, markdown="Script v1")
    assert _statuses(copy) == ["approved"]


# ------------------------------------------------------------------------------ AK2 CSV / TSV


def _upload(client, workspace, raw, name="b36.csv"):
    return client.post(
        f"/workspace/{workspace.pk}/import/csv/",
        {"csv_file": SimpleUploadedFile(name, raw, content_type="text/csv")},
    )


ROWS = [["date", "time", "platform", "caption"], ["2030-01-02", "10:00", "custom", "Ahoj, světe; žluťoučký"]]


@pytest.mark.parametrize(
    "name,delimiter,encoding",
    [
        ("b36.csv", ",", "utf-8"),
        ("b36.csv", ",", "utf-8-sig"),
        ("b36.csv", ";", "utf-8"),
        ("b36.csv", "\t", "utf-8"),
        ("b36.tsv", "\t", "utf-8"),
        ("b36.tsv", "\t", "utf-16"),
        ("b36.csv", ";", "utf-16"),
    ],
)
def test_upload_reads_delimiter_and_encoding(make, name, delimiter, encoding):
    import csv
    import io

    workspace, account, second, actor, client = make()
    buffer = io.StringIO()
    csv.writer(buffer, delimiter=delimiter).writerows(ROWS)
    response = _upload(client, workspace, buffer.getvalue().encode(encoding), name)
    assert response.status_code == 200
    session = client.session[f"csv_import_{workspace.pk}"]
    assert session["headers"] == ROWS[0]
    assert session["rows"] == ROWS[1:]


def test_quoted_header_commas_do_not_decide_the_delimiter(make):
    workspace, account, second, actor, client = make()
    raw = b'"date, local";time;platform;caption\n2030-01-02;10:00;custom;"a, b"\n'
    _upload(client, workspace, raw)
    assert client.session[f"csv_import_{workspace.pk}"]["rows"] == [["2030-01-02", "10:00", "custom", "a, b"]]


@pytest.mark.parametrize(
    "raw",
    [
        "date,caption\n2030-01-02,Žluťoučký kůň\n".encode("cp1250"),
        "date,caption\n2030-01-02,Grüße\n".encode("latin-1"),
        "date,caption\n".encode("utf-16-le"),  # UTF-16 without its byte-order mark
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR",
    ],
)
def test_other_encodings_get_a_message_instead_of_an_error(make, raw):
    workspace, account, second, actor, client = make()
    response = _upload(client, workspace, raw)
    assert response.status_code == 200
    assert "not saved as UTF-8 text" in response.content.decode()
    assert f"csv_import_{workspace.pk}" not in client.session


def test_tsv_import_whole_flow_creates_the_posts(make):
    workspace, account, second, actor, client = make("none")
    day = (timezone.localdate() + timedelta(days=5)).isoformat()
    raw = f"date\ttime\tplatform\tcaption\n{day}\t10:00\tcustom\tB36 tab, with comma\n".encode()
    _upload(client, workspace, raw, "b36.tsv")
    client.post(
        f"/workspace/{workspace.pk}/import/csv/preview/",
        {"map_date": "0", "map_time": "1", "map_platforms": "2", "map_caption": "3"},
    )
    client.post(f"/workspace/{workspace.pk}/import/csv/confirm/")
    assert Post.objects.filter(workspace=workspace, caption="B36 tab, with comma").count() == 1


def test_import_page_describes_what_it_reads(make):
    workspace, account, second, actor, client = make()
    page = client.get(f"/workspace/{workspace.pk}/import/csv/").content.decode()
    assert "Supports .csv (comma or semicolon) and .tsv files saved as UTF-8" in page


# -------------------------------------------------------------------- AK3 taking out of the plan

PLANNERS = {"owner", "manager"}
TEAM = ["owner", "manager", "editor", "contributor"]


def _planned(workspace, account, author, status="scheduled"):
    when = timezone.now() + timedelta(days=3)
    post, (copy,) = _content(workspace, [account], author, status=status, scheduled_at=when)
    post.scheduled_at = when
    post.save(update_fields=["scheduled_at"])
    return post, copy


@pytest.fixture
def stranger(make):
    def build(workspace):
        user = User.objects.create_user(
            email=f"b36-author-{workspace.pk}@example.invalid", tos_accepted_at=timezone.now()
        )
        WorkspaceMembership.objects.create(user=user, workspace=workspace, workspace_role="owner")
        return user

    return build


@pytest.mark.parametrize("role", TEAM)
@pytest.mark.parametrize("own", [True, False])
@pytest.mark.parametrize("status", ["scheduled", "approved"])
def test_bulk_draft_of_planned_content_needs_the_planning_right(make, stranger, role, own, status):
    workspace, account, second, actor, client = make("none", role)
    author = actor if own else stranger(workspace)
    post, copy = _planned(workspace, account, author, status)
    response = client.post(
        f"/workspace/{workspace.pk}/calendar/bulk-action/", {"action": "draft", "platform_post_ids": [str(copy.pk)]}
    )
    copy.refresh_from_db()
    allowed = role in PLANNERS
    can_edit = own or role in {"owner", "manager", "editor"}  # contributor lacks edit_others_posts
    if allowed:
        assert response.status_code == 200 and response.json()["count"] == 1
        assert copy.status == "draft" and copy.scheduled_at is None
    else:
        assert copy.status == status and copy.scheduled_at is not None
        assert response.status_code == (403 if can_edit else 200)


def test_bulk_draft_refuses_the_whole_batch(make, stranger):
    workspace, account, second, actor, client = make("none", "editor")
    _, planned = _planned(workspace, account, actor)
    _, unplanned = _content(workspace, [second], actor, status="approved")
    response = client.post(
        f"/workspace/{workspace.pk}/calendar/bulk-action/",
        {"action": "draft", "platform_post_ids": [str(planned.pk), str(unplanned[0].pk)]},
    )
    assert response.status_code == 403
    assert "take it out of the plan" in response.json()["error"]
    assert _statuses(planned, unplanned[0]) == ["scheduled", "approved"]


@pytest.mark.parametrize("role", TEAM)
def test_composer_chip_back_to_draft(make, role):
    workspace, account, second, actor, client = make("none", role)
    post, copy = _planned(workspace, account, actor)
    response = client.post(
        f"/workspace/{workspace.pk}/compose/{post.pk}/platform-posts/{copy.pk}/transition/", {"target_status": "draft"}
    )
    copy.refresh_from_db()
    if role in PLANNERS:
        assert response.status_code == 200 and copy.status == "draft"
    else:
        assert response.status_code == 403 and copy.status == "scheduled"


@pytest.mark.parametrize("role", TEAM)
def test_rest_and_mcp_cancel(make, role):
    workspace, account, second, actor, client = make("none", role)
    post, copy = _planned(workspace, account, actor)
    api = _api(workspace, [account], actor)
    response = api.post(f"/api/v1/posts/{post.pk}/cancel")
    copy.refresh_from_db()
    if role in PLANNERS:
        assert response.status_code == 200 and copy.status == "draft"
        return
    assert response.status_code == 403 and copy.status == "scheduled"
    envelope = _mcp(api, "cancel_post", {"post_id": str(post.pk)})
    assert "publish_directly" in json.dumps(envelope)
    assert _statuses(copy) == ["scheduled"]


@pytest.mark.parametrize("role", TEAM)
def test_jr_board_offers_production_stages_for_planned_content_only_to_planners(make, role):
    from apps.jrc_production.services import available_moves, content_membership

    workspace, account, second, actor, client = make("none", role)
    post, copy = _planned(workspace, account, actor)
    moves = available_moves(post, content_membership(workspace, actor))
    assert ("editing" in moves) == (role in PLANNERS)
    unplanned, _ = _content(workspace, [second], actor, status="approved")
    assert "editing" in available_moves(unplanned, content_membership(workspace, actor))


@pytest.mark.parametrize("role", ["owner", "manager"])
def test_queue_removal_follows_the_same_rule(make, stranger, role):
    from apps.calendar.models import Queue, QueueEntry

    workspace, account, second, actor, client = make("none", role)
    post, copy = _planned(workspace, account, actor)
    queue = Queue.objects.create(workspace=workspace, social_account=account, name="B36 queue")
    entry = QueueEntry.objects.create(queue=queue, post=post, position=0, assigned_slot_datetime=copy.scheduled_at)
    response = client.post(f"/workspace/{workspace.pk}/calendar/queues/{queue.pk}/entries/{entry.pk}/remove/")
    assert response.status_code in (200, 204)
    assert _statuses(copy) == ["draft"]


def test_upload_error_replaces_only_the_import_container(make):
    """The form swaps into ``#csv-import-container``; an error must not nest a whole page there."""
    workspace, account, second, actor, client = make()
    response = client.post(
        f"/workspace/{workspace.pk}/import/csv/",
        {"csv_file": SimpleUploadedFile("b36.csv", "a,b\n".encode("utf-16-le"), content_type="text/csv")},
        HTTP_HX_REQUEST="true",
    )
    assert response["HX-Reselect"] == "#csv-import-container"
    assert response["HX-Reswap"] == "outerHTML"
    assert 'id="csv-import-container"' in response.content.decode()


def test_failed_copy_is_not_in_the_plan(make):
    """A failed copy's time is its past attempt; an editor may still move it back to a draft."""
    workspace, account, second, actor, client = make("none", "editor")
    post, copy = _planned(workspace, account, actor, status="failed")
    response = client.post(
        f"/workspace/{workspace.pk}/calendar/bulk-action/", {"action": "draft", "platform_post_ids": [str(copy.pk)]}
    )
    assert response.status_code == 200 and response.json()["count"] == 1
    assert _statuses(copy) == ["draft"]
