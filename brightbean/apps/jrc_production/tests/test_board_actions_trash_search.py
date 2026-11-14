import json
from datetime import timedelta
from unittest.mock import patch
from uuid import uuid4

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.urls import reverse
from django.utils import timezone

from apps.approvals.models import ApprovalAction, PostComment
from apps.calendar.models import Queue, QueueEntry
from apps.composer.models import PlatformPost, Post
from apps.jrc_channels.services import create_manual_channel
from apps.jrc_production.board_actions import board_move
from apps.jrc_production.models import ShotlistItem, VideoDeletionRequest
from apps.jrc_production.services import create_content, move_content, pipeline_stage
from apps.jrc_production.trash import delete_remote_video, manage_trash, trash_content
from apps.jrc_texts.services import create_initial_concept
from apps.jrc_video.client import FreeFrameError
from apps.jrc_video.models import VideoAsset
from apps.members.models import WorkspaceMembership
from apps.workspaces.models import Workspace

pytestmark = pytest.mark.django_db

ROLES = ["owner", "manager", "editor", "contributor", "client", "viewer"]
MODES = ["none", "optional", "required_internal", "required_internal_and_client"]


@pytest.fixture
def content(workspace, user):
    channel = create_manual_channel(workspace, "custom", "Board test channel")
    return create_content(workspace, "A long content title", user, social_account_ids=[channel.pk])


def role(workspace, user, value):
    membership = WorkspaceMembership.objects.get(workspace=workspace, user=user)
    membership.workspace_role = value
    membership.save()
    return membership


def batch(client, user, contents, action, **extra):
    client.force_login(user)
    return client.post(
        reverse("jrc_production:board_actions"),
        data=json.dumps(
            {
                "action": action,
                "items": [{"id": str(post.pk), "expected_stage": pipeline_stage(post)} for post in contents],
                **extra,
            }
        ),
        content_type="application/json",
    )


@pytest.mark.parametrize("actor_role", ROLES)
@pytest.mark.parametrize("target", ["script", "review", "ready", "approved"])
def test_batch_move_rules_and_request_approval(content, workspace, user, client, actor_role, target):
    role(workspace, user, actor_role)
    response = batch(
        client,
        user,
        [content],
        "move",
        stage=target,
        scheduled_at=(timezone.now() + timedelta(days=2)).strftime("%Y-%m-%dT%H:%M"),
    )
    assert response.status_code == 200
    result = response.json()["results"][0]
    allowed = actor_role != "viewer" and (target != "ready" or actor_role in {"owner", "manager"})
    assert result["ok"] is allowed
    content.refresh_from_db()
    if allowed:
        expected = target
        if target == "approved" and actor_role not in {"owner", "manager"}:
            expected = "review"
            assert "Approval requested" in result["message"]
            assert ApprovalAction.objects.filter(post=content, action="submitted").exists()
        assert pipeline_stage(content) == expected
    else:
        assert pipeline_stage(content) == "idea"


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("actor_role", ROLES)
def test_bulk_approval_modes_and_roles(content, workspace, user, client, actor_role, mode):
    workspace.approval_workflow_mode = mode
    workspace.save()
    copy = content.platform_posts.get()
    copy.status = "pending_client" if mode == "required_internal_and_client" else "pending_review"
    copy.save()
    role(workspace, user, actor_role)
    response = batch(client, user, [content], "approve", comment="Batch approval note")
    assert response.status_code == 200
    # Pending copies left by a mode change stay decidable by the team; clients decide only at the client step.
    allowed = actor_role == "client" if mode == "required_internal_and_client" else actor_role in {"owner", "manager"}
    assert response.json()["results"][0]["ok"] is allowed
    copy.refresh_from_db()
    assert copy.status == (
        "approved" if allowed else ("pending_client" if mode == "required_internal_and_client" else "pending_review")
    )
    if allowed:
        assert ApprovalAction.objects.get(post=content, action="approved").comment == "Batch approval note"


def test_two_stage_team_approval_does_not_skip_client(content, workspace, user, client):
    workspace.approval_workflow_mode = "required_internal_and_client"
    workspace.save()
    response = batch(client, user, [content], "move", stage="approved", comment="Team decision")
    assert response.json()["results"][0]["ok"]
    assert "client's approval" in response.json()["results"][0]["message"]
    assert content.platform_posts.get().status == "pending_client"
    assert ApprovalAction.objects.get(post=content, action="approved").comment == "Team decision"


def test_partial_batch_and_stale_stage(content, workspace, user, client):
    other = create_content(workspace, "Second idea", user)
    move_content(other, "script", user, expected_stage="idea")
    # The selection still carries Idea for both rows.
    other.refresh_from_db()
    client.force_login(user)
    response = client.post(
        reverse("jrc_production:board_actions"),
        data=json.dumps(
            {
                "action": "move",
                "stage": "shooting",
                "items": [
                    {"id": str(content.pk), "expected_stage": "idea"},
                    {"id": str(other.pk), "expected_stage": "idea"},
                    {"id": str(uuid4()), "expected_stage": "idea"},
                ],
            }
        ),
        content_type="application/json",
    )
    assert [row["ok"] for row in response.json()["results"]] == [True, False, False]
    assert pipeline_stage(Post.objects.get(pk=content.pk)) == "shooting"
    assert pipeline_stage(Post.objects.get(pk=other.pk)) == "script"


@pytest.mark.parametrize("action", ["move", "approve", "trash", "restore", "purge"])
def test_batch_never_writes_foreign_workspace(content, workspace, organization, user, client, action):
    foreign = Workspace.objects.create(organization=organization, name="Private workspace")
    post = Post.all_objects.create(
        workspace=foreign,
        author=user,
        title="SECRET NEEDLE",
        trashed_at=timezone.now() if action in {"restore", "purge"} else None,
    )
    response = batch(client, user, [post], action, stage="script")
    assert response.status_code == 200
    assert not response.json()["results"][0]["ok"]
    assert "SECRET" not in response.content.decode()
    assert Post.all_objects.filter(pk=post.pk).exists()


@pytest.mark.parametrize(
    "payload",
    [
        {},
        [],
        {"action": "move", "items": []},
        {"action": "purge", "items": [{"id": "bad"}]},
        {"action": "move", "stage": "evil", "items": [{"id": str(uuid4())}]},
        {"action": "move", "stage": "ready", "scheduled_at": [], "items": [{"id": str(uuid4())}]},
    ],
)
def test_batch_rejects_malformed_input(user, client, payload):
    client.force_login(user)
    assert (
        client.post(
            reverse("jrc_production:board_actions"), json.dumps(payload), content_type="application/json"
        ).status_code
        == 400
    )


def test_batch_is_post_only_and_csrf_protected(user, client):
    from django.test import Client

    client.force_login(user)
    assert client.get(reverse("jrc_production:board_actions")).status_code == 405
    strict = Client(enforce_csrf_checks=True)
    strict.force_login(user)
    assert (
        strict.post(reverse("jrc_production:board_actions"), "{}", content_type="application/json").status_code == 403
    )


@pytest.mark.parametrize("actor_role", ROLES)
@pytest.mark.parametrize("planned", [False, True])
def test_trash_uses_deletion_rights(content, workspace, user, client, actor_role, planned):
    if planned:
        copy = content.platform_posts.get()
        copy.status = "scheduled"
        copy.scheduled_at = timezone.now() + timedelta(days=1)
        copy.save()
    role(workspace, user, actor_role)
    response = batch(client, user, [content], "trash")
    allowed = actor_role in ({"owner", "manager"} if planned else {"owner", "manager", "editor", "contributor"})
    assert response.json()["results"][0]["ok"] is allowed
    assert Post.objects.filter(pk=content.pk).exists() is not allowed
    assert Post.all_objects.filter(pk=content.pk).exists()


def test_trash_cancels_schedule_queue_and_retry_preserves_saved_work(content, workspace, user, client):
    copy = content.platform_posts.get()
    copy.status = "scheduled"
    copy.scheduled_at = timezone.now() + timedelta(days=1)
    copy.next_retry_at = timezone.now() + timedelta(days=2)
    copy.retry_count = 3
    copy.save()
    queue = Queue.objects.create(workspace=workspace, social_account=copy.social_account, name="Test queue")
    QueueEntry.objects.create(queue=queue, post=content, position=0)
    document = create_initial_concept(content, user, "Saved concept needle")
    video = VideoAsset.objects.create(platform_post=copy, ff_asset_id=uuid4())
    trash_content(content, user)
    assert not Post.objects.filter(pk=content.pk).exists()
    assert not PlatformPost.objects.filter(pk=copy.pk).exists()
    assert not QueueEntry.objects.filter(post_id=content.pk).exists()
    copy = PlatformPost.all_objects.get(pk=copy.pk)
    assert (copy.status, copy.scheduled_at, copy.next_retry_at) == ("draft", None, None)
    assert VideoAsset.objects.filter(pk=video.pk).exists()
    assert content.jrc_texts.filter(pk=document.document_id).exists()
    client.force_login(user)
    assert str(content.pk) not in client.get(reverse("jrc_production:board")).content.decode()
    assert client.get(reverse("jrc_production:content_detail", args=[workspace.pk, content.pk])).status_code == 404
    assert "A long content title" in client.get(reverse("jrc_production:trash")).content.decode()
    manage_trash(content.pk, user)
    restored = Post.objects.get(pk=content.pk)
    assert restored.scheduled_at is None
    assert restored.platform_posts.get().status == "draft"


def test_purge_requires_trash_and_deletes_children(content, workspace, user):
    with pytest.raises(Post.DoesNotExist):
        manage_trash(content.pk, user, permanently=True)
    trash_content(content, user)
    manage_trash(content.pk, user, permanently=True)
    assert not Post.all_objects.filter(pk=content.pk).exists()
    assert not PlatformPost.all_objects.filter(post_id=content.pk).exists()


@pytest.mark.parametrize("action", ["restore", "purge"])
@pytest.mark.parametrize("actor_role", ROLES)
def test_restore_and_purge_rights(content, workspace, user, client, action, actor_role):
    trash_content(content, user)
    role(workspace, user, actor_role)
    response = batch(client, user, [content], action)
    assert response.json()["results"][0]["ok"] is (actor_role in {"owner", "manager", "editor", "contributor"})


def test_publishing_refuses_trash_and_cannot_be_restored_to_plan(content, user):
    copy = content.platform_posts.get()
    copy.status = "publishing"
    copy.save()
    with pytest.raises(ValidationError, match="publishing"):
        trash_content(content, user)
    assert Post.objects.filter(pk=content.pk).exists()


def test_stale_or_inactive_actor_is_refused(content, workspace, user):
    with pytest.raises(ValidationError, match="changed"):
        board_move(content, "script", user, expected_stage="editing")
    user.is_active = False
    user.save()
    with pytest.raises(PermissionDenied):
        trash_content(content, user)


@pytest.mark.parametrize(
    "source",
    [
        "title",
        "caption",
        "channel_caption",
        "concept",
        "shotlist",
        "shotlist_note",
        "discussion",
        "discussion_reply",
        "video_comment",
        "video_reply",
    ],
)
def test_full_search_all_sources(content, workspace, user, client, source):
    needle = "NeedleBoardSearch"
    remote = []
    if source in {"title", "caption"}:
        setattr(content, source, needle)
        content.save()
    elif source == "channel_caption":
        copy = content.platform_posts.get()
        copy.platform_specific_caption = needle
        copy.save()
    elif source == "concept":
        create_initial_concept(content, user, needle)
    elif source.startswith("shotlist"):
        ShotlistItem.objects.create(
            post=content,
            text=needle if source == "shotlist" else "Shot",
            note=needle if source == "shotlist_note" else "",
        )
    elif source.startswith("discussion"):
        parent = PostComment.objects.create(
            post=content, author=user, body=needle if source == "discussion" else "Thread", visibility="external"
        )
        if source.endswith("reply"):
            PostComment.objects.create(
                post=content, author=user, body=needle, parent_comment=parent, visibility="external"
            )
    else:
        VideoAsset.objects.create(platform_post=content.platform_posts.get(), ff_asset_id=uuid4())
        remote = [
            {
                "body": needle if source == "video_comment" else "Thread",
                "replies": [{"body": needle, "replies": []}] if source.endswith("reply") else [],
            }
        ]
    client.force_login(user)
    with patch("apps.jrc_production.search.FreeFrameUserClient.request", return_value=remote) as api:
        response = client.get(reverse("jrc_production:board"), {"q": needle.lower()})
    assert response.status_code == 200
    assert response.context["total"] == 1
    if source.startswith("video"):
        assert api.call_args.args[0] == "GET"
        assert "/comments" in api.call_args.args[1]


@pytest.mark.parametrize("actor_role", ROLES)
def test_search_hides_internal_deleted_or_foreign_threads(content, workspace, organization, user, client, actor_role):
    role(workspace, user, actor_role)
    internal = PostComment.objects.create(post=content, author=user, body="HiddenBoardNeedle", visibility="internal")
    PostComment.objects.create(
        post=content, author=user, parent_comment=internal, body="HiddenBoardNeedle", visibility="external"
    )
    PostComment.objects.create(
        post=content, author=user, body="DeletedBoardNeedle", visibility="external", deleted_at=timezone.now()
    )
    private = Workspace.objects.create(organization=organization, name="Private")
    Post.objects.create(workspace=private, title="ForeignBoardNeedle")
    client.force_login(user)
    for needle in ["DeletedBoardNeedle", "ForeignBoardNeedle", "HiddenBoardNeedle"]:
        response = client.get(reverse("jrc_production:board"), {"q": needle})
        assert response.status_code == 200
        assert response.context["total"] == int(
            needle == "HiddenBoardNeedle" and actor_role not in {"client", "viewer"}
        )


def test_search_reports_video_api_outage_without_hiding_local_matches(content, workspace, user, client):
    # The video's own content does not match locally, so FreeFrame is asked; another content does match.
    VideoAsset.objects.create(platform_post=content.platform_posts.get(), ff_asset_id=uuid4())
    channel = create_manual_channel(workspace, "custom", "Local match channel")
    create_content(workspace, "Local outage needle", user, social_account_ids=[channel.pk])
    client.force_login(user)
    with patch("apps.jrc_production.search.FreeFrameUserClient.request", side_effect=FreeFrameError("unavailable")):
        response = client.get(reverse("jrc_production:board"), {"q": "outage needle"})
    assert response.context["total"] == 1
    assert "could not all be searched" in response.context["search_warning"]


@pytest.mark.parametrize("status", [403, 404])
def test_one_missing_video_does_not_stop_search_of_remaining_comments(content, workspace, user, client, status):
    channel = create_manual_channel(workspace, "custom", "Second video search")
    other = create_content(workspace, "Second video content", user, social_account_ids=[channel.pk])
    for post in [content, other]:
        VideoAsset.objects.create(platform_post=post.platform_posts.get(), ff_asset_id=uuid4())
    client.force_login(user)
    with patch(
        "apps.jrc_production.search.FreeFrameUserClient.request",
        side_effect=[FreeFrameError("Not accessible", status_code=status), [{"body": "Remaining video needle"}]],
    ) as api:
        response = client.get(reverse("jrc_production:board"), {"q": "Remaining video needle"})
    assert api.call_count == 2
    assert response.context["total"] == 1
    assert response.context["search_warning"]


@pytest.mark.parametrize("actor_role", ROLES)
def test_private_draft_search_requires_current_edit_rights(content, workspace, user, client, actor_role):
    from apps.jrc_texts.models import TextDocument, TextVersion

    document = TextDocument.objects.create(post=content, workspace=workspace, kind="concept", format="plain")
    TextVersion.objects.create(
        document=document,
        number=1,
        state="draft",
        draft_owner=user,
        created_by=user,
        content={"text": "Private draft needle"},
        text="Private draft needle",
    )
    role(workspace, user, actor_role)
    client.force_login(user)
    response = client.get(reverse("jrc_production:board"), {"q": "Private draft needle"})
    assert response.context["total"] == int(actor_role not in {"client", "viewer"})


def test_search_never_reads_another_editors_draft(content, workspace, user, client):
    from apps.accounts.models import User
    from apps.jrc_texts.models import TextDocument, TextVersion

    other = User.objects.create_user(email="draft-p2b09@example.invalid")
    document = TextDocument.objects.create(post=content, workspace=workspace, kind="concept", format="plain")
    TextVersion.objects.create(
        document=document,
        number=1,
        state="draft",
        draft_owner=other,
        created_by=other,
        content={"text": "Other editor private needle"},
        text="Other editor private needle",
    )
    client.force_login(user)
    assert client.get(reverse("jrc_production:board"), {"q": "Other editor private needle"}).context["total"] == 0


def test_search_does_not_request_videos_of_foreign_or_trashed_content(content, workspace, organization, user, client):
    VideoAsset.objects.create(platform_post=content.platform_posts.get(), ff_asset_id=uuid4())
    trash_content(content, user)
    private = Workspace.objects.create(organization=organization, name="Private")
    account = create_manual_channel(private, "custom", "Private")
    post = Post.objects.create(workspace=private, title="Private")
    VideoAsset.objects.create(
        platform_post=PlatformPost.objects.create(post=post, social_account=account), ff_asset_id=uuid4()
    )
    client.force_login(user)
    with patch("apps.jrc_production.search.FreeFrameUserClient.request") as api:
        response = client.get(reverse("jrc_production:board"), {"q": "private"})
    assert response.context["total"] == 0
    api.assert_not_called()


def test_trash_hides_rest_mcp_and_publisher(content, workspace, user):
    from apps.jrc_production.tests.test_review_import_unschedule import _api, _mcp
    from apps.publisher.engine import PublishEngine

    copy = content.platform_posts.get()
    api = _api(workspace, [copy.social_account], user)
    assert api.get(f"/api/v1/posts/{content.pk}").status_code == 200
    trash_content(content, user)
    assert api.get(f"/api/v1/posts/{content.pk}").status_code == 404
    result = _mcp(api, "get_post", {"post_id": str(content.pk)})
    assert "error" in result or result.get("result", {}).get("isError")
    PlatformPost.all_objects.filter(pk=copy.pk).update(
        status="scheduled", scheduled_at=timezone.now() - timedelta(days=1)
    )
    assert copy.pk not in {row.pk for row in PublishEngine()._get_due_platform_posts()}
    # A publisher may already have loaded this group before the content went to Trash.
    assert PublishEngine()._publish_post_group(content, [copy]) is False


def test_purge_enqueues_remote_cleanup_only_after_commit(content, user, django_capture_on_commit_callbacks):
    asset = uuid4()
    VideoAsset.objects.create(platform_post=content.platform_posts.get(), ff_asset_id=asset)
    trash_content(content, user)
    with patch("apps.jrc_production.trash.delete_remote_video") as cleanup:
        with django_capture_on_commit_callbacks(execute=True):
            manage_trash(content.pk, user, permanently=True)
            assert VideoDeletionRequest.objects.filter(pk=asset).exists()
            cleanup.assert_not_called()
        cleanup.assert_called_once_with(asset)
    assert not Post.all_objects.filter(pk=content.pk).exists()


@pytest.mark.parametrize("outcome", ["success", "already_removed", "unavailable"])
def test_remote_cleanup_retry_is_durable_and_idempotent(outcome):
    from unittest.mock import Mock

    asset = uuid4()
    VideoDeletionRequest.objects.create(ff_asset_id=asset)
    api = Mock()
    if outcome != "success":
        api.request.side_effect = FreeFrameError(
            "unavailable", status_code=404 if outcome == "already_removed" else 503
        )
    assert delete_remote_video(asset, api=api) is (outcome != "unavailable")
    api.request.assert_called_once_with("DELETE", f"/assets/{asset}")
    assert VideoDeletionRequest.objects.filter(pk=asset).exists() is (outcome == "unavailable")
