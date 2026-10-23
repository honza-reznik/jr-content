from datetime import timedelta
from unittest.mock import patch

import pytest
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.approvals.models import ApprovalAction
from apps.composer.models import PlatformPost, Post
from apps.jrc_channels.services import create_manual_channel
from apps.jrc_production.models import StageChange
from apps.jrc_production.services import create_content, move_content, pipeline_stage
from apps.members.models import CustomRole, WorkspaceMembership
from apps.workspaces.models import Workspace

pytestmark = pytest.mark.django_db


@pytest.fixture
def channel(workspace):
    return create_manual_channel(workspace, "instagram", "Ella Instagram")


@pytest.fixture
def post(workspace, user, channel):
    return create_content(workspace, "Ella's studio tour", user)


@pytest.fixture
def http(client, user):
    client.force_login(user)
    return client


def set_role(workspace, user, role, custom=None):
    return WorkspaceMembership.objects.update_or_create(
        workspace=workspace, user=user, defaults={"workspace_role": role, "custom_role": custom}
    )[0]


def move_url(post):
    return reverse("jrc_production:move", kwargs={"workspace_id": post.workspace_id, "post_id": post.id})


def review(post, user):
    return move_content(post, "review", user, expected_stage="idea")


def approve(post, user):
    review(post, user)
    return move_content(post, "approved", user, expected_stage="review")


@pytest.mark.parametrize("role", ["owner", "manager", "editor", "contributor", "client"])
def test_members_can_create_content_in_service_and_http(workspace, user, channel, http, role):
    set_role(workspace, user, role)
    content = create_content(workspace, f"{role} idea", user)
    assert content.platform_posts.get().status == "draft"
    response = http.post(reverse("jrc_production:create"), {"workspace": str(workspace.pk), "title": "New idea"})
    assert response.status_code == 302
    assert Post.objects.count() == 2


@pytest.mark.parametrize("scenario", ["outsider", "viewer", "custom_viewer", "archived", "anonymous", "inactive"])
def test_creation_rejects_unauthorized_callers_before_writes(workspace, user, channel, scenario):
    actor = user
    if scenario == "outsider":
        WorkspaceMembership.objects.filter(workspace=workspace, user=user).delete()
    elif scenario == "viewer":
        set_role(workspace, user, "viewer")
    elif scenario == "custom_viewer":
        custom = CustomRole.objects.create(organization=workspace.organization, name="Read only", permissions={})
        set_role(workspace, user, "client", custom)
    elif scenario == "archived":
        workspace.is_archived = True
        workspace.save()
    elif scenario == "anonymous":
        actor = AnonymousUser()
    elif scenario == "inactive":
        user.is_active = False
        user.save()
    with pytest.raises(PermissionDenied):
        create_content(workspace, "Forbidden idea", actor)
    assert not Post.objects.exists()
    assert not PlatformPost.objects.exists()


@pytest.mark.parametrize("role", ["viewer", "outsider", "custom_viewer"])
def test_http_creation_rejects_forbidden_client(workspace, user, channel, http, role):
    if role == "outsider":
        WorkspaceMembership.objects.filter(workspace=workspace, user=user).delete()
    elif role == "custom_viewer":
        custom = CustomRole.objects.create(organization=workspace.organization, name="No creation", permissions={})
        set_role(workspace, user, "client", custom)
    else:
        set_role(workspace, user, role)
    response = http.post(reverse("jrc_production:create"), {"workspace": str(workspace.id), "title": "Hidden"})
    assert response.status_code == 403
    assert not Post.objects.exists()


def test_foreign_workspace_is_rejected_in_service_and_http(workspace, user, http, organization):
    foreign = Workspace.objects.create(organization=organization, name="Foreign client")
    create_manual_channel(foreign, "instagram", "Secret channel")
    with pytest.raises(PermissionDenied):
        create_content(foreign, "Forbidden content", user)
    response = http.post(reverse("jrc_production:create"), {"workspace": str(foreign.id), "title": "Hidden"})
    assert response.status_code == 403
    assert not Post.objects.exists()


def test_board_filters_and_scope(http, workspace, organization, user, post):
    other = Workspace.objects.create(organization=organization, name="Other client")
    set_role(other, user, "owner")
    custom = create_manual_channel(other, "custom", "Other newsletter")
    other_post = create_content(other, "Other public idea", user)
    hidden = Workspace.objects.create(organization=organization, name="Secret client")
    Post.objects.create(workspace=hidden, title="Secret idea")
    url = reverse("jrc_production:board")
    response = http.get(url)
    assert response.status_code == 200
    assert [c["value"] for c in response.context["columns"]] == [
        "idea",
        "script",
        "shooting",
        "editing",
        "review",
        "approved",
        "ready",
        "published",
    ]
    assert response.context["total"] == 2
    assert b"Secret" not in response.content
    response = http.get(url, {"client": workspace.pk})
    assert response.context["total"] == 1
    assert b"Other public idea" not in response.content
    response = http.get(url, {"channel": custom.pk})
    assert response.context["total"] == 1
    assert str(other_post.pk).encode() in response.content
    assert str(post.pk).encode() not in response.content
    for filters in (
        {"client": hidden.pk},
        {"client": "invalid"},
        {"channel": "invalid"},
        {"client": workspace.pk, "channel": custom.pk},
    ):
        assert http.get(url, filters).status_code == 400


def test_board_empty_client_and_viewer_permissions(http, workspace, user):
    # Signup also creates a personal workspace where the new user is owner.
    WorkspaceMembership.objects.filter(user=user).exclude(workspace=workspace).delete()
    set_role(workspace, user, "viewer")
    response = http.get(reverse("jrc_production:board"))
    assert response.status_code == 200
    assert response.context["total"] == 0
    assert not response.context["creatable_clients"]
    assert b"Add to Idea" not in response.content


def test_board_does_not_duplicate_multichannel_content(http, post, workspace):
    extra = create_manual_channel(workspace, "custom", "Ella newsletter")
    PlatformPost.objects.create(post=post, social_account=extra)
    response = http.get(reverse("jrc_production:board"))
    assert response.context["total"] == 1
    assert response.content.count(f'data-post="{post.pk}"'.encode()) == 1


def test_creation_error_preserves_selected_client_and_title(http, workspace, organization, user):
    other = Workspace.objects.create(organization=organization, name="Another client")
    WorkspaceMembership.objects.create(workspace=other, user=user, workspace_role="owner")
    response = http.post(reverse("jrc_production:create"), {"workspace": workspace.pk, "title": "Keep my title"})
    assert response.status_code == 400
    assert b"Enable at least one channel" in response.content
    assert b"Keep my title" in response.content
    assert response.context["selected_client"] == str(workspace.pk)
    assert response.context["new_title"] == "Keep my title"
    assert f'<option value="{workspace.pk}" selected>'.encode() in response.content
    assert f'<option value="{other.pk}" selected>'.encode() not in response.content
    assert not Post.objects.exists()


def test_empty_title_rejected(http, workspace, channel, user):
    with pytest.raises(ValidationError, match="title"):
        create_content(workspace, "   ", user)
    assert http.post(reverse("jrc_production:create"), {"workspace": workspace.pk, "title": " "}).status_code == 400
    assert not Post.objects.exists()


def test_production_moves_record_history_without_changing_editorial_state(post, user):
    for previous, stage in (("idea", "script"), ("script", "shooting"), ("shooting", "editing")):
        changed = move_content(post, stage, user, expected_stage=previous)
        assert pipeline_stage(changed) == stage
        assert changed.platform_posts.get().status == "draft"
        assert changed.jrc_production.stage_changed_by == user
    assert list(StageChange.objects.values_list("from_stage", "to_stage")) == [
        ("idea", "script"),
        ("script", "shooting"),
        ("shooting", "editing"),
    ]


def test_review_and_approval_use_brightbean_services(post, user):
    with patch(
        "apps.jrc_production.services.approvals.submit_for_review",
        wraps=__import__("apps.approvals.services", fromlist=["submit_for_review"]).submit_for_review,
    ) as submit:
        changed = review(post, user)
        submit.assert_called_once()
    assert pipeline_stage(changed) == "review"
    changed = move_content(post, "approved", user, expected_stage="review")
    assert pipeline_stage(changed) == "approved"
    assert set(ApprovalAction.objects.values_list("action", flat=True)) == {"submitted", "approved"}


@pytest.mark.parametrize("mode", ["none", "required_internal", "required_internal_and_client"])
def test_manual_plan_uses_approved_state_and_syncs_every_channel(post, workspace, user, mode):
    extra = create_manual_channel(workspace, "custom", "Ella Forendors")
    PlatformPost.objects.create(post=post, social_account=extra)
    workspace.approval_workflow_mode = mode
    workspace.save()
    approve(post, user)
    if mode == "required_internal_and_client":
        set_role(workspace, user, "client")
        move_content(post, "approved", user, expected_stage="review")
        set_role(workspace, user, "owner")
    when = timezone.now() + timedelta(days=2)
    changed = move_content(post, "ready", user, expected_stage="approved", scheduled_at=when)
    assert pipeline_stage(changed) == "ready"
    assert changed.scheduled_at == when
    assert set(changed.platform_posts.values_list("status", flat=True)) == {"scheduled"}
    assert set(changed.platform_posts.values_list("scheduled_at", flat=True)) == {when}


def test_two_stage_flow_requires_client_and_internal_roles(post, workspace, user):
    workspace.approval_workflow_mode = "required_internal_and_client"
    workspace.save()
    review(post, user)
    set_role(workspace, user, "client")
    with pytest.raises(PermissionDenied):
        move_content(post, "approved", user, expected_stage="review")
    set_role(workspace, user, "owner")
    changed = move_content(post, "approved", user, expected_stage="review")
    assert pipeline_stage(changed) == "review"
    assert changed.platform_posts.get().status == "pending_client"
    with pytest.raises(PermissionDenied):
        move_content(post, "approved", user, expected_stage="review")
    set_role(workspace, user, "client")
    assert pipeline_stage(move_content(post, "approved", user, expected_stage="review")) == "approved"


@pytest.mark.parametrize(
    "role,stage,initial",
    [
        ("viewer", "script", "idea"),
        ("contributor", "approved", "review"),
        ("client", "ready", "approved"),
        ("editor", "ready", "approved"),
        ("owner", "published", "approved"),
        ("owner", "ready", "idea"),
        ("owner", "approved", "idea"),
    ],
)
def test_forbidden_move_is_atomic_in_service_and_http(post, workspace, user, http, role, stage, initial):
    if initial == "review":
        review(post, user)
    elif initial == "approved":
        approve(post, user)
    before = list(post.platform_posts.values_list("status", flat=True))
    count = StageChange.objects.count()
    set_role(workspace, user, role)
    with pytest.raises(PermissionDenied):
        move_content(post, stage, user, expected_stage=initial)
    response = http.post(move_url(post), {"stage": stage, "expected_stage": initial})
    assert response.status_code == 403
    assert list(post.platform_posts.values_list("status", flat=True)) == before
    assert StageChange.objects.count() == count


def test_client_can_move_only_own_production(post, workspace, user):
    client_user = User.objects.create_user(email="ella@example.invalid", password="test", name="Ella")
    set_role(workspace, client_user, "client")
    with pytest.raises(PermissionDenied):
        move_content(post, "script", client_user, expected_stage="idea")
    own = create_content(workspace, "Ella's own idea", client_user)
    assert pipeline_stage(move_content(own, "script", client_user, expected_stage="idea")) == "script"
    assert pipeline_stage(move_content(own, "review", client_user, expected_stage="script")) == "review"


@pytest.mark.parametrize("when", [None, "past", "naive"])
def test_invalid_plan_leaves_every_channel_approved(post, user, when):
    approve(post, user)
    if when == "past":
        when = timezone.now() - timedelta(days=1)
    elif when == "naive":
        when = timezone.now().replace(tzinfo=None) + timedelta(days=1)
    with pytest.raises(ValidationError, match="future"):
        move_content(post, "ready", user, expected_stage="approved", scheduled_at=when)
    assert post.platform_posts.get().status == "approved"
    post.refresh_from_db()
    assert post.scheduled_at is None


def test_stale_move_does_not_overwrite_changes(post, user, http):
    move_content(post, "script", user, expected_stage="idea")
    with pytest.raises(ValidationError, match="changed"):
        move_content(post, "shooting", user, expected_stage="idea")
    response = http.post(move_url(post), {"stage": "shooting", "expected_stage": "idea"}, follow=True)
    assert b"This content changed" in response.content
    assert Post.objects.get(pk=post.pk).jrc_production.stage == "script"


def test_return_from_ready_clears_plan_using_brightbean(post, user):
    approve(post, user)
    move_content(post, "ready", user, expected_stage="approved", scheduled_at=timezone.now() + timedelta(days=1))
    changed = move_content(post, "editing", user, expected_stage="ready")
    assert changed.scheduled_at is None
    assert changed.platform_posts.get().scheduled_at is None
    assert pipeline_stage(changed) == "editing"


def test_failed_second_channel_rolls_back_entire_plan(post, workspace, user):
    extra = create_manual_channel(workspace, "custom", "Second channel")
    PlatformPost.objects.create(post=post, social_account=extra)
    approve(post, user)
    count = StageChange.objects.count()
    original = PlatformPost.save
    attempts = []

    def fail_second(instance, *args, **kwargs):
        attempts.append(instance.pk)
        if len(attempts) == 2:
            raise RuntimeError("Second channel failed")
        return original(instance, *args, **kwargs)

    with patch.object(PlatformPost, "save", fail_second), pytest.raises(RuntimeError, match="Second channel"):
        move_content(post, "ready", user, expected_stage="approved", scheduled_at=timezone.now() + timedelta(days=1))
    assert len(attempts) == 2
    assert set(post.platform_posts.values_list("status", flat=True)) == {"approved"}
    assert set(post.platform_posts.values_list("scheduled_at", flat=True)) == {None}
    post.refresh_from_db()
    assert post.scheduled_at is None
    assert StageChange.objects.count() == count


def test_http_move_and_filter_preservation(post, http, workspace):
    response = http.post(move_url(post), {"stage": "script", "expected_stage": "idea", "client": workspace.pk})
    assert response.status_code == 302
    assert f"client={workspace.pk}" in response.url
    assert Post.objects.get(pk=post.pk).jrc_production.stage == "script"


def test_http_plan_parses_client_timezone(post, workspace, user, http):
    workspace.timezone = "Europe/Prague"
    workspace.save()
    approve(post, user)
    response = http.post(
        move_url(post), {"stage": "ready", "expected_stage": "approved", "scheduled_at": "2030-01-05T12:30"}
    )
    assert response.status_code == 302
    post.refresh_from_db()
    assert post.scheduled_at.isoformat() == "2030-01-05T11:30:00+00:00"


def test_foreign_post_cannot_move_even_with_forged_workspace(post, workspace, organization, user, http):
    other = Workspace.objects.create(organization=organization, name="Other")
    set_role(other, user, "owner")
    url = reverse("jrc_production:move", kwargs={"workspace_id": other.pk, "post_id": post.pk})
    assert http.post(url, {"stage": "script", "expected_stage": "idea"}).status_code == 404
    WorkspaceMembership.objects.filter(workspace=workspace, user=user).delete()
    with pytest.raises(PermissionDenied):
        move_content(post, "script", user, expected_stage="idea")
    assert http.post(move_url(post), {"stage": "script", "expected_stage": "idea"}).status_code == 403


def test_writes_require_post_login_and_csrf(post, user, http):
    assert http.get(reverse("jrc_production:create")).status_code == 405
    assert http.get(move_url(post)).status_code == 405
    anonymous = Client()
    assert anonymous.get(reverse("jrc_production:board")).status_code == 302
    secure = Client(enforce_csrf_checks=True)
    secure.force_login(user)
    assert secure.post(move_url(post), {"stage": "script", "expected_stage": "idea"}).status_code == 403
    assert (
        secure.post(reverse("jrc_production:create"), {"workspace": post.workspace_id, "title": "No CSRF"}).status_code
        == 403
    )
