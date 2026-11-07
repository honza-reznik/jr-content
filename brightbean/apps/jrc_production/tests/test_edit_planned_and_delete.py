# JR Content: přidáno 2026-11-07 (blok P1-B37), AGPL-3.0.
"""Edits of planned / held content, new final video or thumbnail after approval, one deletion rule (P1-B37).

Expectations are written by hand, not derived from ``apps.approvals.edits`` or ``apps.members.permissions``:

* PM decision P-025 A with its extension of 7 Nov: in the modes that require approval ("Internal review
  required" and the two-stage mode) changing what goes out of a channel copy that carries an approval
  (approved, waiting for the client, planned, on hold, failed) sends it back to review on every path; a
  planned copy is taken out of the plan (its time and queue slot dropped, the time kept as the proposed
  date) and the editor is told so. "No approvals" and "Optional approvals" change nothing.
* Marking another final video version or uploading a new thumbnail after approval is such a change.
* Deleting planned content (bulk Delete, composer Delete, a deselected channel, removing a channel)
  needs the right to take it out of the plan: owner and manager may, editor and contributor may not.
  Unplanned content stays under edit rights.
* Removing a queue entry whose copy is not planned takes nothing out of the plan; REST and MCP name
  both rights a refused unscheduling needs.
"""

import json
from datetime import timedelta
from io import BytesIO
from urllib.parse import unquote
from uuid import uuid4

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.utils import timezone
from PIL import Image

from apps.accounts.models import User
from apps.calendar.models import Queue, QueueEntry
from apps.composer.models import PlatformPost, Post
from apps.jrc_channels.services import create_manual_channel
from apps.jrc_production.tests.test_review_import_unschedule import MODES, RETURNS, _api, _content, _mcp, _statuses
from apps.jrc_video.models import VideoAsset, VideoVersion
from apps.members.models import CustomRole, OrgMembership, WorkspaceMembership
from apps.workspaces.models import Workspace

pytestmark = pytest.mark.django_db

HELD = ["approved", "pending_client", "scheduled", "on_hold", "failed"]
REQUIRED = ["required_internal", "required_internal_and_client"]
PLANNERS = {"owner", "manager"}
TEAM = ["owner", "manager", "editor", "contributor"]


@pytest.fixture
def make(organization):
    """A workspace in ``mode`` with two manual channels and a member in ``role`` (as P1-B36's fixture)."""
    counter = {"n": 0}

    def build(mode="required_internal_and_client", role="owner"):
        counter["n"] += 1
        n = counter["n"]
        workspace = Workspace.objects.create(
            organization=organization, name=f"B37 {mode} {role} {n}", approval_workflow_mode=mode
        )
        account = create_manual_channel(workspace, "custom", f"B37 channel {n}")
        second = create_manual_channel(workspace, "custom", f"B37 second {n}")
        actor = User.objects.create_user(
            email=f"b37-{role}-{n}-{uuid4().hex[:6]}@example.invalid",
            tos_accepted_at=timezone.now(),
            name=f"B37 {role}",
        )
        OrgMembership.objects.create(
            user=actor, organization=organization, org_role="owner" if role == "owner" else "member"
        )
        WorkspaceMembership.objects.create(user=actor, workspace=workspace, workspace_role=role)
        client = Client()
        client.force_login(actor)
        return workspace, account, second, actor, client

    return build


@pytest.fixture
def stranger():
    """Another author (owner) of the workspace, for content that is not the actor's own."""

    def build(workspace):
        user = User.objects.create_user(
            email=f"b37-author-{uuid4().hex[:8]}@example.invalid", tos_accepted_at=timezone.now()
        )
        WorkspaceMembership.objects.create(user=user, workspace=workspace, workspace_role="owner")
        return user

    return build


@pytest.fixture(autouse=True)
def local_storage(settings):
    settings.STORAGES = {**settings.STORAGES, "default": {"BACKEND": "django.core.files.storage.InMemoryStorage"}}


def _held(workspace, accounts, author, status, *, days=3):
    """Content whose copies are in ``status``; planned and failed copies carry a time (failed: in the past)."""
    when = timezone.now() + timedelta(days=-1 if status == "failed" else days)
    timed = status in ("scheduled", "failed")
    post, copies = _content(workspace, accounts, author, status=status, scheduled_at=when if timed else None)
    if timed:
        post.scheduled_at = when
        post.save(update_fields=["scheduled_at"])
    return post, copies, when


def _detail_save(client, workspace, post, copy, text):
    return client.post(
        f"/workspace/{workspace.pk}/content/{post.pk}/?tab=caption",
        {"action": "save_caption", "platform_post": str(copy.pk), "caption": text},
    )


def _composer_save(client, workspace, post, accounts, action="save_draft", **fields):
    data = {
        "action": action,
        "title": post.title,
        "caption": post.caption,
        "first_comment": post.first_comment,
        "selected_accounts": ",".join(str(a.pk) for a in accounts),
        **fields,
    }
    return client.post(f"/workspace/{workspace.pk}/compose/{post.pk}/save/", data, HTTP_HX_REQUEST="true")


def _notice(response):
    return unquote(response.get("X-JRC-Review-Notice", ""))


# ------------------------------------------------------------------ AK1 edits of held content


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("status", HELD)
def test_caption_edit_of_held_copy_returns_it_to_review_where_required(make, mode, status):
    workspace, account, second, actor, client = make(mode)
    post, (copy, other), when = _held(workspace, [account, second], actor, status)
    response = _detail_save(client, workspace, post, copy, "Fixed typo")
    assert response.status_code == 302
    copy.refresh_from_db()
    other.refresh_from_db()
    assert copy.platform_specific_caption == "Fixed typo"
    assert other.status == status  # the other channel publishes what was approved
    if RETURNS[mode]:
        assert copy.status == "pending_review"
        assert copy.scheduled_at is None  # out of the plan (failed: its past attempt is gone too)
    else:
        assert copy.status == status
        assert copy.scheduled_at == (when if status in ("scheduled", "failed") else None)


@pytest.mark.parametrize("mode", REQUIRED)
def test_planned_content_leaves_the_plan_with_a_clear_message(make, mode):
    workspace, account, second, actor, client = make(mode)
    post, (copy,), when = _held(workspace, [account], actor, "scheduled")
    queue = Queue.objects.create(workspace=workspace, social_account=account, name="B37 queue")
    QueueEntry.objects.create(queue=queue, post=post, position=0, assigned_slot_datetime=when)
    page = client.get(f"/workspace/{workspace.pk}/content/{post.pk}/?tab=caption").content.decode()
    assert "Planned · saving a changed caption takes it out of the plan and sends it back for re-approval" in page
    response = _detail_save(client, workspace, post, copy, "Fixed typo")
    page = client.get(response["Location"]).content.decode()
    assert f"{account.account_name} was taken out of the plan because the caption changed." in page
    assert "plan it again once it is approved." in page
    copy.refresh_from_db()
    post.refresh_from_db()
    assert (copy.status, copy.scheduled_at) == ("pending_review", None)
    assert post.scheduled_at is None
    assert post.proposed_publish_at == when  # the planned date stays as a proposal
    assert not QueueEntry.objects.filter(post=post).exists()


def test_other_planned_channel_keeps_the_plan_and_no_proposal(make):
    workspace, account, second, actor, client = make("required_internal")
    post, (copy, other), when = _held(workspace, [account, second], actor, "scheduled")
    _detail_save(client, workspace, post, copy, "Only this channel")
    other.refresh_from_db()
    post.refresh_from_db()
    assert (other.status, other.scheduled_at) == ("scheduled", when)
    assert post.scheduled_at == when and post.proposed_publish_at is None


def test_approved_copy_with_a_planned_time_leaves_the_plan(make):
    """An approved chip dragged onto the calendar carries a time without being scheduled; it is planned too."""
    workspace, account, second, actor, client = make("required_internal")
    when = timezone.now() + timedelta(days=4)
    post, (copy,) = _content(workspace, [account], actor, status="approved", scheduled_at=when)
    _detail_save(client, workspace, post, copy, "Changed")
    copy.refresh_from_db()
    assert (copy.status, copy.scheduled_at) == ("pending_review", None)


@pytest.mark.parametrize("status", ["scheduled", "pending_client", "on_hold"])
def test_saving_the_same_caption_keeps_held_copy(make, status):
    workspace, account, second, actor, client = make("required_internal_and_client")
    post, (copy,), when = _held(workspace, [account], actor, status)
    _detail_save(client, workspace, post, copy, "Base caption")
    copy.refresh_from_db()
    assert copy.status == status


@pytest.mark.parametrize("path", ["rest", "mcp"])
@pytest.mark.parametrize("mode", REQUIRED)
def test_api_caption_write_takes_planned_copy_out_of_the_plan_and_says_so(make, mode, path):
    workspace, account, second, actor, client = make(mode)
    post, (copy,), when = _held(workspace, [account], actor, "scheduled")
    api = _api(workspace, [account], actor)
    if path == "rest":
        response = api.post(
            f"/api/v1/posts/{post.pk}/captions/{copy.pk}",
            data=json.dumps({"text": "Fixed typo"}),
            content_type="application/json",
        )
        assert response.status_code == 201
        notice = response.json()["review_notice"]
    else:
        result = _mcp(api, "jrc_save_caption", {"post_id": str(post.pk), "platform_post_id": str(copy.pk), "text": "X"})
        notice = json.loads(result["result"]["content"][0]["text"])["review_notice"]
    assert "taken out of the plan because the caption changed" in notice
    assert _statuses(copy) == ["pending_review"]


def test_api_caption_write_without_return_has_an_empty_notice(make):
    workspace, account, second, actor, client = make("optional")
    post, (copy,), when = _held(workspace, [account], actor, "scheduled")
    api = _api(workspace, [account], actor)
    response = api.post(
        f"/api/v1/posts/{post.pk}/captions/{copy.pk}", data=json.dumps({"text": "New"}), content_type="application/json"
    )
    assert response.json()["review_notice"] == ""
    assert _statuses(copy) == ["scheduled"]


def test_rest_patch_of_planned_content(make):
    workspace, account, second, actor, client = make("required_internal")
    post, (copy,), when = _held(workspace, [account], actor, "scheduled")
    api = _api(workspace, [account], actor)
    response = api.patch(
        f"/api/v1/posts/{post.pk}", data=json.dumps({"title": "New title"}), content_type="application/json"
    )
    assert response.status_code == 200
    body = response.json()
    assert "taken out of the plan because the content changed" in body["review_notice"]
    assert body["scheduled_at"] is None
    copy.refresh_from_db()
    assert (copy.status, copy.scheduled_at) == ("pending_review", None)


def test_composer_scheduled_hint_where_edits_keep_the_plan(make):
    workspace, account, second, actor, client = make("optional")
    post, (copy,), when = _held(workspace, [account], actor, "scheduled")
    page = client.get(f"/workspace/{workspace.pk}/compose/{post.pk}/").content.decode()
    assert "Saving changes will affect the scheduled post." in page
    assert "takes it out of the plan" not in page


def test_composer_save_of_planned_content_says_so(make):
    workspace, account, second, actor, client = make("required_internal")
    post, (copy,), when = _held(workspace, [account], actor, "scheduled")
    page = client.get(f"/workspace/{workspace.pk}/compose/{post.pk}/").content.decode()
    assert "Saving changes takes it out of the plan and sends it back for re-approval." in page
    response = _composer_save(client, workspace, post, [account], caption="Edited in the composer")
    assert response.status_code == 204
    assert "taken out of the plan because the content changed" in _notice(response)
    copy.refresh_from_db()
    assert (copy.status, copy.scheduled_at) == ("pending_review", None)


@pytest.mark.parametrize("action", ["schedule", "publish_now", "submit_for_approval", "resubmit_for_approval"])
def test_composer_release_actions_never_carry_an_edited_copy_out(make, action):
    """Every save action applies the rule first; an edited approved copy is not planned in the same save."""
    workspace, account, second, actor, client = make("required_internal")
    post, (copy,) = _content(workspace, [account], actor, status="approved")
    day = timezone.localtime() + timedelta(days=2)
    response = _composer_save(
        client,
        workspace,
        post,
        [account],
        action=action,
        caption="Edited and released",
        scheduled_date=day.date().isoformat(),
        scheduled_time="10:00",
    )
    assert response.status_code == 204, response.content
    assert "goes back to review for approval because the content changed" in _notice(response)
    copy.refresh_from_db()
    assert (copy.status, copy.scheduled_at) == ("pending_review", None)


def test_composer_queue_action_does_not_queue_an_edited_copy(make):
    workspace, account, second, actor, client = make("required_internal")
    post, (copy,) = _content(workspace, [account], actor, status="approved")
    queue = Queue.objects.create(workspace=workspace, social_account=account, name="B37 queue")
    response = _composer_save(
        client, workspace, post, [account], action="add_to_queue", caption="Edited", queue_id=str(queue.pk)
    )
    assert response.status_code == 204, response.content
    copy.refresh_from_db()
    assert (copy.status, copy.scheduled_at) == ("pending_review", None)
    assert not QueueEntry.objects.filter(post=post).exists()


def test_composer_autosave_of_planned_content(make):
    workspace, account, second, actor, client = make("required_internal")
    post, (copy,), when = _held(workspace, [account], actor, "scheduled")
    response = client.post(
        f"/workspace/{workspace.pk}/compose/{post.pk}/autosave/",
        {
            "title": post.title,
            "caption": "Autosaved edit",
            "first_comment": post.first_comment,
            "selected_accounts": str(account.pk),
        },
        HTTP_HX_REQUEST="true",
    )
    assert response.status_code == 200
    assert "taken out of the plan" in _notice(response)
    assert _statuses(copy) == ["pending_review"]


def test_review_return_is_not_a_general_transition(make):
    """Only the edit rule moves planned content to review; the composer chip's "submit" does not."""
    workspace, account, second, actor, client = make("required_internal")
    post, (copy,), when = _held(workspace, [account], actor, "scheduled")
    client.post(
        f"/workspace/{workspace.pk}/compose/{post.pk}/platform-posts/{copy.pk}/transition/",
        {"target_status": "pending_review"},
    )
    copy.refresh_from_db()
    assert (copy.status, copy.scheduled_at) == ("scheduled", when)
    assert not copy.can_transition_to("pending_review")


def test_edited_failed_copy_cannot_be_retried_unreviewed(make):
    workspace, account, second, actor, client = make("required_internal")
    post, (copy,), when = _held(workspace, [account], actor, "failed")
    _detail_save(client, workspace, post, copy, "Fixed after the failure")
    response = client.post(
        f"/workspace/{workspace.pk}/calendar/bulk-action/", {"action": "publish", "platform_post_ids": [str(copy.pk)]}
    )
    # A copy in review is not released by "Publish": nothing moves.
    assert response.json().get("count", 0) == 0
    assert _statuses(copy) == ["pending_review"]


# -------------------------------------------------------------- AK2 final video and thumbnail


def _video(copy, *numbers):
    video = VideoAsset.objects.create(platform_post=copy, ff_asset_id=uuid4())
    versions = [
        VideoVersion.objects.create(video=video, number=n, ff_version_id=uuid4(), status=VideoVersion.Status.READY)
        for n in numbers
    ]
    return video, versions


def _mark_final(client, workspace, post, version, final=True):
    return client.post(
        f"/workspace/{workspace.pk}/content/{post.pk}/video/versions/{version.pk}/final/",
        data=json.dumps({"final": final}),
        content_type="application/json",
    )


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("status", ["approved", "scheduled", "pending_client"])
def test_new_final_version_after_approval(make, mode, status):
    workspace, account, second, actor, client = make(mode)
    post, (copy, other), when = _held(workspace, [account, second], actor, status)
    video, (v1, v2) = _video(copy, 1, 2)
    video.final_version = v1
    video.save(update_fields=["final_version"])
    response = _mark_final(client, workspace, post, v2)
    assert response.status_code == 200, response.content
    video.refresh_from_db()
    assert video.final_version_id == v2.pk
    expected = "pending_review" if RETURNS[mode] else status
    assert _statuses(copy, other) == [expected, status]
    notice = response.json().get("review_notice", "")
    if RETURNS[mode]:
        assert "because the final video changed" in notice
        assert ("taken out of the plan" in notice) == (status == "scheduled")
    else:
        assert notice == ""


def test_remarking_the_same_final_keeps_the_approval(make):
    workspace, account, second, actor, client = make("required_internal")
    post, (copy,) = _content(workspace, [account], actor, status="approved")
    video, (v1,) = _video(copy, 1)
    video.final_version = v1
    video.save(update_fields=["final_version"])
    assert "review_notice" not in _mark_final(client, workspace, post, v1).json()
    assert _statuses(copy) == ["approved"]


def test_first_final_mark_and_removal_after_approval_are_changes(make):
    """What goes out is the final version: choosing it, or taking it away, after approval needs a new review."""
    workspace, account, second, actor, client = make("required_internal")
    post, (copy,) = _content(workspace, [account], actor, status="approved")
    video, (v1,) = _video(copy, 1)
    _mark_final(client, workspace, post, v1)
    assert _statuses(copy) == ["pending_review"]
    copy.status = "approved"
    copy.save(update_fields=["status"])
    _mark_final(client, workspace, post, v1, final=False)
    assert _statuses(copy) == ["pending_review"]


def _image():
    stream = BytesIO()
    Image.new("RGB", (16, 9), "orange").save(stream, format="PNG")
    return SimpleUploadedFile("nahled.png", stream.getvalue(), content_type="image/png")


@pytest.mark.parametrize("mode", MODES)
def test_new_thumbnail_after_approval(make, mode):
    workspace, account, second, actor, client = make(mode)
    post, (copy, other), when = _held(workspace, [account, second], actor, "scheduled")
    response = client.post(
        f"/workspace/{workspace.pk}/content/{post.pk}/channels/{copy.pk}/thumbnail/", {"image": _image()}
    )
    assert response.status_code == 302
    expected = "pending_review" if RETURNS[mode] else "scheduled"
    assert _statuses(copy, other) == [expected, "scheduled"]
    page = client.get(response["Location"]).content.decode()
    assert "Thumbnail uploaded as version 1." in page
    assert ("taken out of the plan because the thumbnail changed" in page) == RETURNS[mode]


def test_platform_extra_is_not_reviewed_content(make):
    """Provider settings of automatic channels are not what a JR reviewer approves (section 4)."""
    from apps.approvals.edits import return_edited_to_review, reviewed_content

    workspace, account, second, actor, client = make("required_internal")
    post, (copy,) = _content(workspace, [account], actor, status="approved")
    before = reviewed_content(post)
    copy.platform_extra = {"privacy_status": "private"}
    copy.save(update_fields=["platform_extra"])
    assert return_edited_to_review(post, before) == []


# --------------------------------------------------------------------- AK3 one deletion rule


def _planned(workspace, account, author):
    post, (copy,), when = _held(workspace, [account], author, "scheduled")
    return post, copy


@pytest.mark.parametrize("role", TEAM)
@pytest.mark.parametrize("own", [True, False])
def test_bulk_delete_of_planned_content_needs_the_planning_right(make, stranger, role, own):
    workspace, account, second, actor, client = make("none", role)
    author = actor if own else stranger(workspace)
    post, copy = _planned(workspace, account, author)
    response = client.post(
        f"/workspace/{workspace.pk}/calendar/bulk-action/", {"action": "delete", "platform_post_ids": [str(copy.pk)]}
    )
    exists = PlatformPost.objects.filter(pk=copy.pk).exists()
    can_edit = own or role != "contributor"
    if role in PLANNERS:
        assert response.status_code == 200 and response.json()["count"] == 1 and not exists
    elif can_edit:
        assert response.status_code == 403 and "nothing was deleted" in response.json()["error"]
        assert exists
    else:
        assert exists  # another author's content, no edit right: untouched as before


@pytest.mark.parametrize("role", ["editor", "contributor"])
def test_bulk_delete_of_unplanned_own_content_stays_with_edit_rights(make, role):
    workspace, account, second, actor, client = make("none", role)
    post, (copy,) = _content(workspace, [account], actor, status="draft")
    response = client.post(
        f"/workspace/{workspace.pk}/calendar/bulk-action/", {"action": "delete", "platform_post_ids": [str(copy.pk)]}
    )
    assert response.json()["count"] == 1
    assert not PlatformPost.objects.filter(pk=copy.pk).exists()


@pytest.mark.parametrize("role", TEAM)
@pytest.mark.parametrize("scope", ["post", "channel"])
def test_composer_delete_of_planned_content(make, role, scope):
    workspace, account, second, actor, client = make("none", role)
    post, copy = _planned(workspace, account, actor)
    query = f"?account={account.pk}" if scope == "channel" else ""
    response = client.post(f"/workspace/{workspace.pk}/compose/{post.pk}/delete/{query}")
    if role in PLANNERS:
        assert response.status_code == 204
        assert not PlatformPost.objects.filter(pk=copy.pk).exists()
    else:
        assert response.status_code == 403
        assert "deleting takes it out of the plan" in response.json()["error"]
        assert PlatformPost.objects.filter(pk=copy.pk).exists() and Post.objects.filter(pk=post.pk).exists()


def test_composer_delete_of_unplanned_content_by_editor(make):
    workspace, account, second, actor, client = make("none", "editor")
    post, _ = _content(workspace, [account], actor, status="draft")
    assert client.post(f"/workspace/{workspace.pk}/compose/{post.pk}/delete/").status_code == 204
    assert not Post.objects.filter(pk=post.pk).exists()


@pytest.mark.parametrize("role", ["editor", "owner"])
def test_deselecting_a_planned_channel_in_the_composer(make, role):
    workspace, account, second, actor, client = make("none", role)
    post, (copy, kept), when = _held(workspace, [account, second], actor, "scheduled")
    response = _composer_save(client, workspace, post, [second])
    exists = PlatformPost.objects.filter(pk=copy.pk).exists()
    if role == "editor":
        assert response.status_code == 409
        assert "removing a channel deletes it" in response.json()["errors"]["selected_accounts"]
        assert exists
    else:
        assert response.status_code == 204 and not exists


@pytest.mark.parametrize("role", ["editor", "owner"])
@pytest.mark.parametrize("path", ["detail", "rest"])
def test_removing_a_draft_channel_with_a_planned_time(make, role, path):
    """A draft copy carrying a time is planned (``is_planned``): removing it takes it out of the plan."""
    workspace, account, second, actor, client = make("none", role)
    when = timezone.now() + timedelta(days=2)
    post, (copy, kept) = _content(workspace, [account, second], actor, status="draft")
    copy.scheduled_at = when
    copy.save(update_fields=["scheduled_at"])
    if path == "detail":
        response = client.post(
            f"/workspace/{workspace.pk}/content/{post.pk}/",
            {"action": "remove_channel", "platform_post": str(copy.pk)},
        )
        refused = response.status_code == 200 and "deleting takes it out of the plan" in response.content.decode()
    else:
        api = _api(workspace, [account, second], actor)
        response = api.delete(f"/api/v1/posts/{post.pk}/channels/{copy.pk}")
        refused = response.status_code == 403 and "deleting takes it out of the plan" in response.json()["detail"]
    exists = PlatformPost.objects.filter(pk=copy.pk).exists()
    if role == "editor":
        assert refused and exists
    else:
        assert not exists


# --------------------------------------------------------------------- AK5 queue and messages


def _queue_planner(workspace, organization):
    """A member who may manage queues (``publish_directly``) but not edit other authors' content."""
    role = CustomRole.objects.create(
        organization=organization,
        name=f"Queue keeper {uuid4().hex[:6]}",
        permissions={"publish_directly": True, "create_posts": True, "view_calendar": True},
    )
    user = User.objects.create_user(
        email=f"b37-queue-{uuid4().hex[:6]}@example.invalid", tos_accepted_at=timezone.now()
    )
    OrgMembership.objects.create(user=user, organization=organization, org_role="member")
    WorkspaceMembership.objects.create(user=user, workspace=workspace, workspace_role="editor", custom_role=role)
    client = Client()
    client.force_login(user)
    return client


@pytest.mark.parametrize("planned", [True, False])
def test_queue_entry_removal_needs_unscheduling_only_for_planned_copies(make, organization, planned):
    workspace, account, second, actor, client = make("none", "owner")
    if planned:
        post, copy = _planned(workspace, account, actor)
    else:
        post, (copy,) = _content(workspace, [account], actor, status="draft")
    queue = Queue.objects.create(workspace=workspace, social_account=account, name="B37 queue")
    entry = QueueEntry.objects.create(queue=queue, post=post, position=0)
    keeper = _queue_planner(workspace, organization)
    response = keeper.post(f"/workspace/{workspace.pk}/calendar/queues/{queue.pk}/entries/{entry.pk}/remove/")
    if planned:
        assert response.status_code == 403
        assert QueueEntry.objects.filter(pk=entry.pk).exists()
        assert _statuses(copy) == ["scheduled"]
    else:
        assert response.status_code in (200, 204)
        assert not QueueEntry.objects.filter(pk=entry.pk).exists()
        assert _statuses(copy) == ["draft"]


@pytest.mark.parametrize("path", ["rest", "mcp"])
def test_refused_cancel_names_both_rights(make, path):
    workspace, account, second, actor, client = make("none", "editor")
    post, copy = _planned(workspace, account, actor)
    api = _api(workspace, [account], actor)
    if path == "rest":
        message = api.post(f"/api/v1/posts/{post.pk}/cancel").json()["detail"]
    else:
        message = _mcp(api, "cancel_post", {"post_id": str(post.pk)})["error"]["message"]
    assert "publish_directly" in message and "edit_others_posts" in message and "out of the plan" in message
    assert _statuses(copy) == ["scheduled"]
