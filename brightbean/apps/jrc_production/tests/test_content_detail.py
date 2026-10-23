import pytest
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.approvals.models import ApprovalAction, PostComment
from apps.jrc_channels.services import create_manual_channel
from apps.jrc_production.models import StageChange
from apps.jrc_production.services import create_content
from apps.members.models import WorkspaceMembership

pytestmark = pytest.mark.django_db


@pytest.fixture
def content_with_history(workspace, user):
    create_manual_channel(workspace, "instagram", "Ella Instagram")
    post = create_content(workspace, "Ella garden reel", user)
    post.caption = "A short story about the garden."
    post.internal_notes = "Team only: confirm the location release."
    post.save(update_fields=["caption", "internal_notes", "updated_at"])
    StageChange.objects.create(
        post=post, from_stage="idea", to_stage="script", user=user, note="Team only: validate the outline."
    )
    ApprovalAction.objects.create(
        post=post,
        user=user,
        action=ApprovalAction.ActionType.SUBMITTED,
        comment="Team only: internal approval feedback.",
    )
    PostComment.objects.create(
        post=post,
        author=user,
        body="Team only: internal discussion comment.",
        visibility=PostComment.Visibility.INTERNAL,
    )
    return post


def _detail_url(workspace, post, tab="script"):
    return (
        reverse("jrc_production:content_detail", kwargs={"workspace_id": workspace.id, "post_id": post.id})
        + f"?tab={tab}"
    )


def test_workspace_admin_can_view_content_tabs_and_combined_history(client, workspace, user, content_with_history):
    post = content_with_history
    WorkspaceMembership.objects.filter(user=user, workspace=workspace).update(workspace_role="manager")
    client.force_login(user)

    response = client.get(_detail_url(workspace, post, "history"))

    assert response.status_code == 200
    assert b"Script" in response.content
    assert b"video" in response.content
    assert b"caption" in response.content
    assert b"approval" in response.content
    assert b"history" in response.content
    assert b"Moved from Idea to Script" in response.content
    assert b"Submitted" in response.content
    assert b"Team only: internal approval feedback." in response.content
    assert b"Team only: confirm the location release." in response.content
    assert b"Team only: validate the outline." in response.content
    assert b"Team only: internal discussion comment." not in response.content


def test_client_can_view_content_but_not_team_notes(client, workspace, content_with_history):
    post = content_with_history
    client_user = User.objects.create_user(
        email="ella@example.test", password="testpass123", name="Ella", tos_accepted_at=timezone.now()
    )
    WorkspaceMembership.objects.create(user=client_user, workspace=workspace, workspace_role="client")
    ApprovalAction.objects.create(
        post=post,
        user=client_user,
        action=ApprovalAction.ActionType.CHANGES_REQUESTED,
        comment="Please adjust the opening shot.",
    )
    client.force_login(client_user)

    response = client.get(_detail_url(workspace, post, "history"))

    assert response.status_code == 200
    assert b"Ella garden reel" in response.content
    assert b"Team only: confirm the location release." not in response.content
    assert b"Team only: validate the outline." not in response.content
    assert b"Team only: internal approval feedback." not in response.content
    assert b"Team only: internal discussion comment." not in response.content
    assert b"Please adjust the opening shot." in response.content


def test_nonmember_cannot_view_content(client, workspace, user, content_with_history):
    WorkspaceMembership.objects.filter(user=user, workspace=workspace).delete()
    client.force_login(user)

    response = client.get(_detail_url(workspace, content_with_history))

    assert response.status_code == 403


def test_team_can_comment_reply_and_resolve(client, workspace, user, content_with_history):
    post = content_with_history
    WorkspaceMembership.objects.filter(user=user, workspace=workspace).update(workspace_role="manager")
    client.force_login(user)

    response = client.post(
        reverse("jrc_production:add_content_comment", kwargs={"workspace_id": workspace.id, "post_id": post.id}),
        {"body": "Team discussion"},
    )

    assert response.status_code == 302
    comment = PostComment.objects.get(post=post, body="Team discussion")
    assert comment.visibility == PostComment.Visibility.INTERNAL
    reply_response = client.post(
        reverse(
            "jrc_production:reply_to_content_comment",
            kwargs={"workspace_id": workspace.id, "post_id": post.id, "comment_id": comment.id},
        ),
        {"body": "Team follow-up"},
    )
    assert reply_response.status_code == 302
    reply = PostComment.objects.get(parent_comment=comment)
    assert reply.visibility == PostComment.Visibility.INTERNAL

    resolve_response = client.post(
        reverse(
            "jrc_production:resolve_content_comment",
            kwargs={"workspace_id": workspace.id, "post_id": post.id, "comment_id": comment.id},
        ),
        {"resolved": "true"},
    )
    assert resolve_response.status_code == 302
    comment.refresh_from_db()
    assert comment.is_resolved is True


def test_client_can_comment_but_cannot_see_or_resolve_internal_threads(client, workspace, user, content_with_history):
    post = content_with_history
    client_user = User.objects.create_user(
        email="ella@example.test", password="testpass123", name="Ella", tos_accepted_at=timezone.now()
    )
    WorkspaceMembership.objects.create(user=client_user, workspace=workspace, workspace_role="client")
    internal = PostComment.objects.create(
        post=post, author=user, body="Private team note", visibility=PostComment.Visibility.INTERNAL
    )
    client.force_login(client_user)

    response = client.post(
        reverse("jrc_production:add_content_comment", kwargs={"workspace_id": workspace.id, "post_id": post.id}),
        {"body": "Client feedback", "visibility": "internal"},
    )
    assert response.status_code == 302
    client_comment = PostComment.objects.get(post=post, body="Client feedback")
    assert client_comment.visibility == PostComment.Visibility.EXTERNAL

    detail = client.get(_detail_url(workspace, post, "comments"))
    assert detail.status_code == 200
    assert b"Client feedback" in detail.content
    assert b"Private team note" not in detail.content
    assert b"Request changes" not in detail.content

    resolve = client.post(
        reverse(
            "jrc_production:resolve_content_comment",
            kwargs={"workspace_id": workspace.id, "post_id": post.id, "comment_id": internal.id},
        ),
        {"resolved": "true"},
    )
    assert resolve.status_code == 403
    internal.refresh_from_db()
    assert internal.is_resolved is False


def test_client_does_not_receive_internal_replies(client, workspace, user, content_with_history):
    post = content_with_history
    client_user = User.objects.create_user(
        email="ella@example.test", password="testpass123", name="Ella", tos_accepted_at=timezone.now()
    )
    WorkspaceMembership.objects.create(user=client_user, workspace=workspace, workspace_role="client")
    parent = PostComment.objects.create(
        post=post, author=user, body="Shared thread", visibility=PostComment.Visibility.EXTERNAL
    )
    PostComment.objects.create(
        post=post,
        author=user,
        parent_comment=parent,
        body="Internal reply",
        visibility=PostComment.Visibility.INTERNAL,
    )
    client.force_login(client_user)

    response = client.get(_detail_url(workspace, post, "comments"))

    assert response.status_code == 200
    assert b"Shared thread" in response.content
    assert b"Internal reply" not in response.content


def test_editor_cannot_resolve_comments(client, workspace, user, content_with_history):
    post = content_with_history
    WorkspaceMembership.objects.filter(user=user, workspace=workspace).update(workspace_role="editor")
    comment = PostComment.objects.create(
        post=post, author=user, body="Open discussion", visibility=PostComment.Visibility.INTERNAL
    )
    client.force_login(user)

    detail = client.get(_detail_url(workspace, post, "comments"))
    assert detail.status_code == 200
    assert b"Mark resolved" not in detail.content

    response = client.post(
        reverse(
            "jrc_production:resolve_content_comment",
            kwargs={"workspace_id": workspace.id, "post_id": post.id, "comment_id": comment.id},
        ),
        {"resolved": "true"},
    )
    assert response.status_code == 403
    comment.refresh_from_db()
    assert comment.is_resolved is False
