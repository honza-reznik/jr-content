"""Independent regression cases from the P1-B25 review (actual HTTP targets)."""

from uuid import uuid4

import pytest
from django.urls import reverse

from apps.accounts.models import User
from apps.approvals.models import ApprovalAction, PostComment
from apps.composer.models import PlatformPost, Post
from apps.jrc_production.models import PostProduction
from apps.members.models import CustomRole, WorkspaceMembership
from apps.notifications.models import Notification
from apps.social_accounts.models import SocialAccount
from apps.workspaces.models import Workspace


@pytest.fixture
def data(organization, org_owner):
    workspace = Workspace.objects.create(
        organization=organization, name="Review", approval_workflow_mode="required_internal_and_client"
    )
    member = WorkspaceMembership.objects.create(workspace=workspace, user=org_owner, workspace_role="owner")
    author = User.objects.create_user(email="review-author@example.invalid")
    post = Post.objects.create(workspace=workspace, author=author, title="Keep", caption="Keep caption")
    PostProduction.objects.create(post=post)
    account = SocialAccount.objects.create(workspace=workspace, platform="instagram", account_name="Local")
    variant = PlatformPost.objects.create(post=post, social_account=account, status="pending_review")
    return workspace, member, post, variant


@pytest.mark.django_db
@pytest.mark.parametrize("source", ["url", "body"])
@pytest.mark.parametrize("role", ["contributor", "editor", "client", "viewer"])
def test_autosave_checks_the_actual_target(client, user, data, source, role):
    ws, member, post, variant = data
    member.workspace_role = role
    member.save()
    client.force_login(user)
    payload = {"title": "Intrusion", "_autosave_post_id": str(post.pk), "selected_accounts": ""}
    url = (
        reverse("composer:autosave_edit", args=[ws.pk, post.pk])
        if source == "url"
        else reverse("composer:autosave", args=[ws.pk])
    )
    response = client.post(url, payload, HTTP_HX_REQUEST="true")
    # Editors can edit foreign content, contributors cannot.
    if role == "editor":
        assert response.status_code == 200
    else:
        assert response.status_code == 403
        post.refresh_from_db()
        variant.refresh_from_db()
        assert post.title == "Keep"
        assert variant.status == "pending_review"


@pytest.mark.django_db
@pytest.mark.parametrize("status", ["approved", "published", "publishing"])
@pytest.mark.parametrize("source", ["url", "body"])
def test_autosave_checks_protected_state_and_rereview(client, user, data, status, source):
    ws, _, post, variant = data
    variant.status = status
    variant.save()
    client.force_login(user)
    url = (
        reverse("composer:autosave_edit", args=[ws.pk, post.pk])
        if source == "url"
        else reverse("composer:autosave", args=[ws.pk])
    )
    response = client.post(
        url,
        {"_autosave_post_id": str(post.pk), "title": "Changed", "selected_accounts": str(variant.social_account_id)},
    )
    assert response.status_code == 200
    post.refresh_from_db()
    variant.refresh_from_db()
    assert post.title == ("Changed" if status == "approved" else "Keep")
    assert variant.status == ("pending_review" if status == "approved" else status)


@pytest.mark.django_db
@pytest.mark.parametrize("mode", ["none", "optional", "required_internal", "required_internal_and_client"])
@pytest.mark.parametrize("status", ["draft", "pending_review", "pending_client"])
@pytest.mark.parametrize("action", ["approve", "request_changes", "reject", "bulk_action"])
def test_client_decides_only_at_client_stage(client, user, data, mode, status, action):
    ws, member, post, variant = data
    ws.approval_workflow_mode = mode
    ws.save()
    member.workspace_role = "client"
    member.save()
    variant.status = status
    variant.save()
    client.force_login(user)
    args = [ws.pk] if action == "bulk_action" else [ws.pk, post.pk]
    response = client.post(
        reverse(f"approvals:{action}", args=args),
        {"action": "approve", "post_ids": [str(post.pk)], "comment": "Please change"},
    )
    variant.refresh_from_db()
    allowed = mode == "required_internal_and_client" and status == "pending_client"
    if allowed:
        assert response.status_code in {200, 204}
        assert variant.status != status
    else:
        assert response.status_code == 403 or (action == "bulk_action" and status == "draft")
        assert variant.status == status
        assert not ApprovalAction.objects.filter(post=post).exists()


@pytest.mark.django_db
@pytest.mark.parametrize(
    "action",
    [
        "add_content_comment",
        "reply_to_content_comment",
        "resolve_content_comment",
        "add_comment",
        "edit_comment",
        "delete_comment",
    ],
)
@pytest.mark.parametrize("role", ["viewer", "client", "editor", "manager"])
def test_comment_role_policy_covers_all_transports(client, user, data, action, role):
    ws, member, post, _ = data
    member.workspace_role = role
    member.save()
    comment = PostComment.objects.create(post=post, author=post.author, body="Keep", visibility="external")
    client.force_login(user)
    namespace = "approvals" if action in {"add_comment", "edit_comment", "delete_comment"} else "jrc_production"
    args = [ws.pk, post.pk]
    if action not in {"add_comment", "add_content_comment"}:
        args.append(comment.pk)
    response = client.post(reverse(f"{namespace}:{action}", args=args), {"body": "New", "resolved": "true"})
    comment.refresh_from_db()
    if role == "viewer":
        assert response.status_code == 403
        assert not comment.is_resolved and comment.deleted_at is None and comment.body == "Keep"
        assert PostComment.objects.filter(post=post).count() == 1
    elif action in {"delete_comment", "resolve_content_comment"} and role in {"client", "editor"}:
        assert response.status_code in {400, 403}
        assert comment.deleted_at is None and not comment.is_resolved
    elif action == "edit_comment":
        assert response.status_code == 403
        assert comment.body == "Keep"
    else:
        assert response.status_code in {200, 302}


@pytest.mark.django_db
def test_custom_viewer_cannot_comment_or_approve(client, user, data):
    ws, member, post, variant = data
    member.workspace_role = "viewer"
    member.custom_role = CustomRole.objects.create(
        organization=ws.organization, name="Forged viewer", permissions={"create_posts": True, "approve_posts": True}
    )
    member.save()
    client.force_login(user)
    for key in ("approvals:approve", "approvals:add_comment", "jrc_production:add_content_comment"):
        assert client.post(reverse(key, args=[ws.pk, post.pk]), {"body": "Denied"}).status_code == 403
    response = client.get(reverse("jrc_production:content_detail", args=[ws.pk, post.pk]) + "?tab=comments")
    assert response.status_code == 200
    assert b'placeholder="Write a comment' not in response.content
    assert b'placeholder="Write a reply' not in response.content
    variant.refresh_from_db()
    assert variant.status == "pending_review"


# Independent payloads and expected rights: no expectations read from WRITE_RULES.
@pytest.mark.django_db
@pytest.mark.parametrize("role", ["client", "viewer", "editor", "nonmember"])
@pytest.mark.parametrize(
    "action", ["move", "content_detail", "mark_published", "approval_decision", "resolve_content_comment"]
)
def test_service_entrypoints_authorize_before_changing_data(client, user, data, role, action):
    ws, member, post, variant = data
    if role == "nonmember":
        member.delete()
    else:
        member.workspace_role = role
        member.save()
    client.force_login(user)
    args = [ws.pk, post.pk]
    if action == "resolve_content_comment":
        args.append(PostComment.objects.create(post=post, author=post.author, body="Keep", visibility="external").pk)
    payload = {
        "title": "Forbidden",
        "action": "set_date" if action == "content_detail" else "approve",
        "decision": "approve",
        "stage": "approved",
        "expected_stage": "review",
        "resolved": "true",
        "platform_post_id": str(variant.pk),
    }
    response = client.post(reverse(f"jrc_production:{action}", args=args), payload)
    expected = {302} if action == "mark_published" and role == "client" else {403, 404}
    assert response.status_code in expected, (role, action, response.status_code)
    post.refresh_from_db()
    variant.refresh_from_db()
    assert post.title == "Keep" and variant.status == "pending_review"


@pytest.mark.django_db
@pytest.mark.parametrize("target", ["invalid", str(uuid4())])
def test_missing_body_target_never_creates_a_replacement_draft(client, user, data, target):
    ws, _, _, _ = data
    before = Post.objects.filter(workspace=ws).count()
    client.force_login(user)
    response = client.post(
        reverse("composer:autosave", args=[ws.pk]), {"_autosave_post_id": target, "title": "Unexpected"}
    )
    assert response.status_code == 404
    assert Post.objects.filter(workspace=ws).count() == before


@pytest.mark.django_db
def test_bulk_decision_rolls_back_if_one_post_is_not_at_client_stage(client, user, data):
    ws, member, post, variant = data
    member.workspace_role = "client"
    member.save()
    allowed = Post.objects.create(workspace=ws, author=post.author)
    pp = PlatformPost.objects.create(post=allowed, social_account=variant.social_account, status="pending_client")
    client.force_login(user)
    notifications = Notification.objects.count()
    response = client.post(
        reverse("approvals:bulk_action", args=[ws.pk]),
        {"action": "approve", "post_ids": [str(allowed.pk), str(post.pk)]},
    )
    assert response.status_code == 403
    pp.refresh_from_db()
    variant.refresh_from_db()
    assert pp.status == "pending_client" and variant.status == "pending_review"
    assert not ApprovalAction.objects.exists()
    # Authorized before the first transition: no notification about an undone approval.
    assert Notification.objects.count() == notifications


@pytest.mark.django_db
@pytest.mark.parametrize("action", ["approve", "reject", "request_changes", "bulk_approve", "bulk_reject"])
def test_team_cannot_decide_for_the_client_in_two_stage_mode(client, user, data, action):
    """The team must not skip the client's sign-off through any upstream approval path."""
    ws, _, post, variant = data
    variant.status = "pending_client"
    variant.save()
    client.force_login(user)
    if action.startswith("bulk_"):
        url = reverse("approvals:bulk_action", args=[ws.pk])
        payload = {"action": action.removeprefix("bulk_"), "post_ids": [str(post.pk)], "comment": "No"}
    else:
        url = reverse(f"approvals:{action}", args=[ws.pk, post.pk])
        payload = {"comment": "No"}
    assert client.post(url, payload).status_code == 403
    variant.refresh_from_db()
    assert variant.status == "pending_client"
    assert not ApprovalAction.objects.filter(post=post).exists()


@pytest.mark.django_db
@pytest.mark.parametrize("mode", ["none", "optional", "required_internal", "required_internal_and_client"])
@pytest.mark.parametrize("status", ["pending_review", "pending_client"])
@pytest.mark.parametrize("role", ["client", "viewer", "owner"])
@pytest.mark.parametrize("decision", ["approve", "request_changes"])
def test_board_decision_uses_the_same_stage_rule(client, user, data, mode, status, role, decision):
    """E-08-ex6b-01: the JR approval card follows the rule of the upstream approval paths."""
    from apps.jrc_production.services import pipeline_stage

    ws, member, post, variant = data
    ws.approval_workflow_mode = mode
    ws.save()
    member.workspace_role = role
    member.save()
    variant.status = status
    variant.save()
    client.force_login(user)
    response = client.post(
        reverse("jrc_production:approval_decision", args=[ws.pk, post.pk]),
        {"decision": decision, "expected_stage": pipeline_stage(post), "comment": "Please change"},
    )
    variant.refresh_from_db()
    two_stage = mode == "required_internal_and_client"
    if role == "client":
        allowed = two_stage and status == "pending_client"
    elif role == "owner":
        allowed = status == "pending_review" or not two_stage
    else:
        allowed = False
    if allowed:
        assert response.status_code == 302
        assert variant.status != status
    else:
        assert response.status_code == 403
        assert variant.status == status
        assert not ApprovalAction.objects.filter(post=post).exists()


@pytest.mark.django_db
def test_team_still_decides_copies_left_at_client_step_after_mode_change(client, user, data):
    ws, _, post, variant = data
    ws.approval_workflow_mode = "required_internal"
    ws.save()
    variant.status = "pending_client"
    variant.save()
    client.force_login(user)
    response = client.post(reverse("approvals:approve", args=[ws.pk, post.pk]))
    assert response.status_code == 204
    variant.refresh_from_db()
    assert variant.status == "approved"


@pytest.mark.django_db
@pytest.mark.parametrize("target", ["draft", "pending_review", "pending_client"])
def test_team_chip_uses_the_same_two_stage_approval_service(client, user, data, target):
    ws, _, post, variant = data
    variant.status = target
    variant.save()
    client.force_login(user)
    response = client.post(
        reverse("composer:transition_platform_post", args=[ws.pk, post.pk, variant.pk]), {"target_status": "approved"}
    )
    variant.refresh_from_db()
    if target == "pending_review":
        assert response.status_code == 200
        assert variant.status == "pending_client"
    else:
        assert response.status_code == 403
        assert variant.status == target


@pytest.mark.django_db
def test_autosave_never_attaches_another_workspaces_account(client, user, data):
    ws, _, post, variant = data
    foreign = Workspace.objects.create(organization=ws.organization, name="Other client")
    account = SocialAccount.objects.create(workspace=foreign, platform="instagram", account_name="Other")
    client.force_login(user)
    response = client.post(
        reverse("composer:autosave", args=[ws.pk]),
        {
            "_autosave_post_id": str(post.pk),
            "title": "Allowed change",
            "selected_accounts": f"{variant.social_account_id},{account.pk}",
        },
    )
    assert response.status_code == 200
    assert list(post.platform_posts.values_list("social_account_id", flat=True)) == [variant.social_account_id]


@pytest.mark.django_db
def test_chip_submission_records_review_history_and_reminder(client, user, data):
    from apps.approvals.models import ApprovalReminder

    ws, _, post, variant = data
    variant.status = "draft"
    variant.save()
    client.force_login(user)
    url = reverse("composer:transition_platform_post", args=[ws.pk, post.pk, variant.pk])
    response = client.post(url, {"target_status": "pending_review"})
    assert response.status_code == 200
    variant.refresh_from_db()
    assert variant.status == "pending_review"
    assert ApprovalAction.objects.filter(post=post, platform_post=variant, action="submitted").count() == 1
    assert ApprovalReminder.objects.filter(post=post, stage="pending_review").exists()
    assert client.post(url, {"target_status": "pending_review"}).status_code == 200
    assert ApprovalAction.objects.filter(post=post, action="submitted").count() == 1
