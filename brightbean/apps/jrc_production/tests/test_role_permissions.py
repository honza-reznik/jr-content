"""Role matrix at the HTTP boundary, including stored history audiences."""

import pytest
from django.core.cache import cache
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.api_keys.services import issue_api_key
from apps.approvals import comments
from apps.approvals.models import ApprovalAction, PostComment
from apps.approvals.services import _record_action
from apps.composer.models import Post
from apps.jrc_channels.services import create_manual_channel
from apps.jrc_production.models import StageChange
from apps.jrc_production.services import create_content, move_content
from apps.jrc_texts import services as texts
from apps.members.models import WorkspaceMembership

pytestmark = pytest.mark.django_db
ROLES = ["owner", "manager", "editor", "contributor", "client", "viewer"]


@pytest.fixture
def content(workspace, user):
    create_manual_channel(workspace, "instagram", "Role test")
    post = create_content(workspace, "Shared content", user)
    post.internal_notes = "SECRET TEAM NOTES"
    post.save()
    document = texts.create_document(post.pk, user, markdown="Saved script")
    draft = texts.edit(document.versions.get(state="final").pk, user)
    texts.save(draft.pk, user, expected_revision=0, markdown="Updated script")
    PostComment.objects.create(post=post, author=user, body="SECRET COMMENT")
    shared = PostComment.objects.create(post=post, author=user, body="Shared comment", visibility="external")
    PostComment.objects.create(post=post, author=user, parent_comment=shared, body="SECRET REPLY")
    return post


@pytest.mark.parametrize("role", ROLES)
@pytest.mark.parametrize("own", [True, False])
def test_text_and_video_role_matrix(client, workspace, user, content, role, own):
    if not own:
        author = User.objects.create_user(email="other-author@example.test")
        Post.objects.filter(pk=content.pk).update(author=author)
    WorkspaceMembership.objects.filter(workspace=workspace, user=user).update(workspace_role=role)
    can_write = role in ("owner", "manager", "editor") or (role == "contributor" and own)
    client.force_login(user)
    detail = reverse("jrc_production:content_detail", args=[workspace.pk, content.pk])
    script = reverse("jrc_texts:script", args=[workspace.pk, content.pk])
    for tab in ("script", "caption", "video", "comments", "history"):
        response = client.get(detail, {"tab": tab})
        assert response.status_code == 200
        assert response.context["can_edit_captions"] == can_write
        if role in ("client", "viewer"):
            assert b"SECRET" not in response.content
        if tab == "caption":
            assert (b"Save version" in response.content) == can_write
        if tab == "comments":
            assert b"Shared comment" in response.content

    state = client.get(script).json()
    assert state["can_edit"] == can_write
    assert state["comparison"]["new"]["content"]
    selected = client.get(script, {"compare_old": state["versions"][1]["id"], "compare_new": state["latest"]})
    assert selected.status_code == 200
    assert client.post(
        script, {"action": "edit", "base": state["latest"]}, content_type="application/json"
    ).status_code == (200 if can_write else 403)
    caption = client.post(
        detail,
        {"action": "save_caption", "platform_post": str(content.platform_posts.get().pk), "caption": "New caption"},
    )
    assert caption.status_code == (302 if can_write else 403)
    if not can_write:
        assert b"Your role does not allow this action" in caption.content
        assert b"Back to content board" in caption.content
        assert b"SECRET" not in caption.content


@pytest.mark.parametrize("role", ROLES)
def test_rest_notes_follow_current_role_even_for_preexisting_key(workspace, user, org_owner, content, role):
    cache.delete("agent_api:auth_fail:127.0.0.1")
    issued = issue_api_key(
        workspace=workspace,
        social_accounts=[content.platform_posts.get().social_account],
        issued_by=user,
        name="Role test",
        permissions=["create_posts", "view_analytics"],
    )
    WorkspaceMembership.objects.filter(workspace=workspace, user=user).update(workspace_role=role)
    http = Client(HTTP_AUTHORIZATION=f"Bearer {issued.plaintext_token}")
    response = http.get(f"/api/v1/posts/{content.pk}", secure=True)
    assert response.status_code == 200, response.content
    assert response.json()["internal_notes"] == ("" if role in ("client", "viewer") else "SECRET TEAM NOTES")
    assert "SECRET COMMENT" not in response.content.decode()
    assert "SECRET REPLY" not in response.content.decode()
    assert "comments" not in response.json()  # This API exposes no comment endpoint.


@pytest.mark.parametrize("initial, later, visible", [("owner", "client", False), ("client", "editor", True)])
def test_approval_and_stage_audience_survives_role_change_and_departure(
    client, workspace, user, content, initial, later, visible
):
    reader = User.objects.create_user(email="history-reader@example.test", tos_accepted_at=timezone.now())
    WorkspaceMembership.objects.create(workspace=workspace, user=reader, workspace_role="client")
    membership = WorkspaceMembership.objects.get(workspace=workspace, user=user)
    membership.workspace_role = initial
    membership.save()
    action = _record_action(content, None, user, "changes_requested", "Historical approval note")
    move_content(content, "script", user, expected_stage="idea")
    change = StageChange.objects.get(post=content)
    change.note = "Historical stage note"
    change.save(update_fields=["note"])
    for row in (action, change):
        assert row.actor_role == initial
        assert row.note_visibility == ("external" if visible else "internal")
    membership.workspace_role = later
    membership.save()
    client.force_login(reader)
    url = reverse("jrc_production:content_detail", args=[workspace.pk, content.pk])
    for departure in (None, "membership", "user"):
        if departure == "membership":
            membership.delete()
        elif departure == "user":
            user.delete()
        response = client.get(url, {"tab": "history"})
        assert response.status_code == 200
        assert (b"Historical approval note" in response.content) == visible
        assert (b"Historical stage note" in response.content) == visible


def test_nonmember_comment_service_returns_no_internal_data(workspace, user, content):
    WorkspaceMembership.objects.filter(workspace=workspace, user=user).delete()
    assert not comments.get_comments_for_post(content, user)


def test_history_default_is_internal_even_if_author_is_now_client(client, workspace, user, content):
    ApprovalAction.objects.create(post=content, user=user, action="approved", comment="SECRET LEGACY APPROVAL")
    StageChange.objects.create(
        post=content, user=user, from_stage="idea", to_stage="script", note="SECRET LEGACY STAGE"
    )
    WorkspaceMembership.objects.filter(workspace=workspace, user=user).update(workspace_role="client")
    client.force_login(user)
    response = client.get(reverse("jrc_production:content_detail", args=[workspace.pk, content.pk]), {"tab": "history"})
    assert response.status_code == 200
    assert b"SECRET" not in response.content
