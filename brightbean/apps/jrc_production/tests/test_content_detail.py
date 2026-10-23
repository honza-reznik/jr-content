import pytest
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.approvals.models import ApprovalAction
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
    ApprovalAction.objects.create(post=post, user=user, action=ApprovalAction.ActionType.SUBMITTED)
    return post


def _detail_url(workspace, post, tab="script"):
    return (
        reverse("jrc_production:content_detail", kwargs={"workspace_id": workspace.id, "post_id": post.id})
        + f"?tab={tab}"
    )


def test_workspace_admin_can_view_content_tabs_and_combined_history(client, workspace, user, content_with_history):
    post = content_with_history
    WorkspaceMembership.objects.create(user=user, workspace=workspace, workspace_role="manager")
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
    assert b"Team only: confirm the location release." in response.content
    assert b"Team only: validate the outline." in response.content


def test_client_can_view_content_but_not_team_notes(client, workspace, content_with_history):
    post = content_with_history
    client_user = User.objects.create_user(
        email="ella@example.test", password="testpass123", name="Ella", tos_accepted_at=timezone.now()
    )
    WorkspaceMembership.objects.create(user=client_user, workspace=workspace, workspace_role="client")
    client.force_login(client_user)

    response = client.get(_detail_url(workspace, post, "history"))

    assert response.status_code == 200
    assert b"Ella garden reel" in response.content
    assert b"Team only: confirm the location release." not in response.content
    assert b"Team only: validate the outline." not in response.content


def test_nonmember_cannot_view_content(client, workspace, user, content_with_history):
    client.force_login(user)

    response = client.get(_detail_url(workspace, content_with_history))

    assert response.status_code == 403
