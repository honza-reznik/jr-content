"""Client approval on the content detail (F7-B02): card, decisions, history, "Waiting for you"."""

import uuid

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.approvals.models import ApprovalAction
from apps.jrc_channels.services import create_manual_channel
from apps.jrc_production.services import (
    available_moves,
    can_decide,
    create_content,
    move_content,
    pipeline_stage,
    request_content_changes,
)
from apps.jrc_texts import services as texts
from apps.jrc_video.models import VideoAsset, VideoVersion
from apps.members.models import CustomRole, WorkspaceMembership
from apps.organizations.models import Organization
from apps.workspaces.models import Workspace

pytestmark = pytest.mark.django_db

SINGLE = "required_internal"
TWO_STAGE = "required_internal_and_client"


def make_user(email, name):
    return User.objects.create_user(email=email, password="testpass123", name=name, tos_accepted_at=timezone.now())


def join(workspace, user, role, custom=None):
    return WorkspaceMembership.objects.update_or_create(
        workspace=workspace, user=user, defaults={"workspace_role": role, "custom_role": custom}
    )[0]


@pytest.fixture
def team(workspace, user):
    return user  # owner of the workspace


@pytest.fixture
def manager(workspace):
    person = make_user("manager@example.test", "Mia Manager")
    join(workspace, person, "manager")
    return person


@pytest.fixture
def ella(workspace):
    person = make_user("ella@example.test", "Ella")
    join(workspace, person, "client")
    return person


@pytest.fixture
def viewer(workspace):
    person = make_user("viewer@example.test", "Vic Viewer")
    join(workspace, person, "viewer")
    return person


@pytest.fixture
def content(workspace, team):
    create_manual_channel(workspace, "instagram", "Ella Instagram")
    post = create_content(workspace, "Ella garden reel", team)
    document = texts.create_document(post.pk, team, markdown="SCENE ONE: Ella waters the roses.")
    draft = texts.edit(document.versions.get(state="final").pk, team)
    texts.save(draft.pk, team, expected_revision=0, markdown="SCENE ONE: Ella waters the roses at dawn.")
    post.internal_notes = "TEAM ONLY: budget is tight"
    post.save(update_fields=["internal_notes", "updated_at"])
    return post


def set_mode(workspace, mode):
    Workspace.objects.filter(pk=workspace.pk).update(approval_workflow_mode=mode)
    workspace.refresh_from_db()


def to_review(post, user):
    return move_content(post, "review", user, expected_stage="idea")


def login(user):
    client = Client()
    client.force_login(user)
    return client


def detail_url(post, tab="approval"):
    return (
        reverse("jrc_production:content_detail", kwargs={"workspace_id": post.workspace_id, "post_id": post.id})
        + f"?tab={tab}"
    )


def decide_url(post):
    return reverse("jrc_production:approval_decision", kwargs={"workspace_id": post.workspace_id, "post_id": post.id})


def decide(user, post, decision, comment="", expected="review"):
    return login(user).post(
        decide_url(post), {"decision": decision, "comment": comment, "expected_stage": expected}, follow=True
    )


def statuses(post):
    return set(post.platform_posts.values_list("status", flat=True))


# ---- AK1: the card -----------------------------------------------------------------------------


def test_card_summarizes_script_caption_and_final_video(workspace, team, ella, content):
    variant = content.platform_posts.get()
    texts.save_caption(content.pk, variant.pk, team, "Roses at dawn #garden")
    video = VideoAsset.objects.create(platform_post=variant, ff_asset_id=uuid.uuid4())
    VideoVersion.objects.create(video=video, number=1, ff_version_id=uuid.uuid4(), status="ready", duration_s=20)
    v2 = VideoVersion.objects.create(video=video, number=2, ff_version_id=uuid.uuid4(), status="ready", duration_s=26)
    video.final_version, video.final_by, video.final_at = v2, team, timezone.now()
    video.save()
    to_review(content, team)

    response = login(ella).get(detail_url(content))

    assert response.status_code == 200
    body = response.content.decode()
    assert "Ella waters the roses at dawn." in body
    assert "Roses at dawn #garden" in body
    assert "Final for publishing" in body and "version 2" in body and "0:26" in body
    assert "Waiting for your decision." in body
    assert 'name="decision" value="approve"' in body
    assert 'name="decision" value="request_changes"' in body
    assert "TEAM ONLY" not in body


def test_card_without_video_or_script_says_so(workspace, team, ella):
    create_manual_channel(workspace, "instagram", "Ella Instagram")
    post = create_content(workspace, "Empty idea", team)
    to_review(post, team)
    body = login(ella).get(detail_url(post)).content.decode()
    assert "No script has been saved yet." in body
    assert "No video yet." in body


# ---- AK1/AK2: single stage ---------------------------------------------------------------------


def test_client_approves_in_single_stage_mode(workspace, team, ella, content):
    set_mode(workspace, SINGLE)
    to_review(content, team)

    response = decide(ella, content, "approve")

    assert response.status_code == 200
    assert "Approved. Thank you." in response.content.decode()
    content.refresh_from_db()
    assert pipeline_stage(content) == "approved"
    action = ApprovalAction.objects.get(post=content, action="approved")
    assert action.user == ella and action.note_visibility == "external" and action.actor_role == "client"
    assert b"Your decision" not in response.content


def test_client_requests_changes_with_required_comment(workspace, team, ella, content):
    set_mode(workspace, SINGLE)
    to_review(content, team)

    empty = decide(ella, content, "request_changes", comment="   ")
    assert "Tell the team what to change" in empty.content.decode()
    assert statuses(content) == {"pending_review"}
    assert not ApprovalAction.objects.filter(post=content, action="changes_requested").exists()

    done = decide(ella, content, "request_changes", comment="Please cut the ending at 0:17.")
    assert "Changes requested." in done.content.decode()
    assert statuses(content) == {"changes_requested"}
    action = ApprovalAction.objects.get(post=content, action="changes_requested")
    assert (action.user, action.comment, action.note_visibility) == (ella, "Please cut the ending at 0:17.", "external")
    # still shown as Review, with the request visible on the card for client and team
    assert pipeline_stage(content) == "review"
    for person in (ella, team):
        body = login(person).get(detail_url(content)).content.decode()
        assert "Please cut the ending at 0:17." in body
        assert "Your decision" not in body or person == team  # nothing pending for the client
    assert "Changes were requested" in login(ella).get(detail_url(content)).content.decode()


def test_changes_then_new_version_then_approval(workspace, team, ella, content):
    set_mode(workspace, SINGLE)
    to_review(content, team)
    decide(ella, content, "request_changes", comment="Shorter, please.")
    assert available_moves(content, WorkspaceMembership.objects.get(user=ella, workspace=workspace)) == []

    document = content.jrc_texts.get(kind="script")
    draft = texts.edit(document.versions.filter(state="final").latest("number").pk, team)
    texts.save(draft.pk, team, expected_revision=0, markdown="SCENE ONE: shorter.")
    move_content(content, "review", team, expected_stage="review")
    assert statuses(content) == {"pending_review"}
    body = login(ella).get(detail_url(content)).content.decode()
    assert "SCENE ONE: shorter." in body and "Waiting for your decision." in body

    decide(ella, content, "approve")
    assert statuses(content) == {"approved"}
    history = login(team).get(detail_url(content, "history")).content.decode()
    assert "Changes Requested" in history and "Shorter, please." in history and "Approved" in history


# ---- AK2: two-stage and permissions on the server ----------------------------------------------


def test_two_stage_client_decides_only_after_the_team(workspace, team, ella, content):
    set_mode(workspace, TWO_STAGE)
    to_review(content, team)
    membership = WorkspaceMembership.objects.get(user=ella, workspace=workspace)

    assert not can_decide(content, membership)
    page = login(ella).get(detail_url(content)).content.decode()
    assert "Your decision" not in page and "checking this first" in page
    for decision in ("approve", "request_changes"):
        response = login(ella).post(
            decide_url(content), {"decision": decision, "comment": "x", "expected_stage": "review"}
        )
        assert response.status_code == 403
    assert statuses(content) == {"pending_review"}

    # the team decides first
    team_page = login(team).get(detail_url(content)).content.decode()
    assert "Your decision" in team_page and "client approves after you" in team_page
    decide(team, content, "approve")
    assert statuses(content) == {"pending_client"}
    assert "Waiting for the client" in login(team).get(detail_url(content)).content.decode()
    assert "Your decision" not in login(team).get(detail_url(content)).content.decode()
    assert login(team).post(decide_url(content), {"decision": "approve", "expected_stage": "review"}).status_code == 403

    assert can_decide(content, membership)
    decide(ella, content, "approve")
    assert statuses(content) == {"approved"}


def test_two_stage_client_can_request_changes_after_the_team(workspace, team, ella, content):
    set_mode(workspace, TWO_STAGE)
    to_review(content, team)
    decide(team, content, "approve")
    decide(ella, content, "request_changes", comment="The caption is too long.")
    assert statuses(content) == {"changes_requested"}


@pytest.mark.parametrize("role", ["viewer", "editor", "contributor"])
def test_roles_without_approve_posts_cannot_decide(workspace, team, content, role):
    set_mode(workspace, SINGLE)
    person = make_user(f"{role}@example.test", role.title())
    join(workspace, person, role)
    to_review(content, team)
    page = login(person).get(detail_url(content))
    assert page.status_code == 200 and b"Your decision" not in page.content
    for decision in ("approve", "request_changes"):
        response = login(person).post(
            decide_url(content), {"decision": decision, "comment": "x", "expected_stage": "review"}
        )
        assert response.status_code == 403
    assert statuses(content) == {"pending_review"}


def test_custom_role_follows_its_own_approve_permission(workspace, team, content):
    set_mode(workspace, SINGLE)
    to_review(content, team)
    allowed = CustomRole.objects.create(
        organization=workspace.organization, name="Approver", permissions={"approve_posts": True, "create_posts": False}
    )
    denied = CustomRole.objects.create(
        organization=workspace.organization, name="Looker", permissions={"approve_posts": False}
    )
    yes, no = make_user("yes@example.test", "Yes"), make_user("no@example.test", "No")
    join(workspace, yes, "editor", allowed)
    join(workspace, no, "editor", denied)
    body = {"decision": "request_changes", "comment": "Change it.", "expected_stage": "review"}
    assert login(no).post(decide_url(content), body).status_code == 403
    assert login(yes).post(decide_url(content), body).status_code == 302
    assert statuses(content) == {"changes_requested"}


def test_other_clients_member_cannot_see_or_decide(workspace, team, content):
    set_mode(workspace, SINGLE)
    to_review(content, team)
    other_org = Organization.objects.create(name="Other org")
    other_ws = Workspace.objects.create(organization=other_org, name="Other client")
    stranger = make_user("stranger@example.test", "Stranger")
    join(other_ws, stranger, "client")
    assert login(stranger).get(detail_url(content)).status_code == 404
    for decision in ("approve", "request_changes"):
        response = login(stranger).post(
            decide_url(content), {"decision": decision, "comment": "x", "expected_stage": "review"}
        )
        assert response.status_code == 404
    assert statuses(content) == {"pending_review"}


def test_decision_requires_login_csrf_and_known_action(workspace, team, ella, content):
    set_mode(workspace, SINGLE)
    to_review(content, team)
    assert Client().post(decide_url(content), {"decision": "approve"}).status_code == 302
    strict = Client(enforce_csrf_checks=True)
    strict.force_login(ella)
    assert strict.post(decide_url(content), {"decision": "approve", "expected_stage": "review"}).status_code == 403
    assert login(ella).get(decide_url(content)).status_code == 405
    assert login(ella).post(decide_url(content), {"decision": "reject", "expected_stage": "review"}).status_code == 400
    assert statuses(content) == {"pending_review"}


def test_double_submit_and_stale_page_are_refused(workspace, team, ella, content):
    set_mode(workspace, SINGLE)
    to_review(content, team)
    decide(ella, content, "approve")
    again = decide(ella, content, "approve")
    assert "This content changed" in again.content.decode()
    assert ApprovalAction.objects.filter(post=content, action="approved").count() == 1
    # content that is no longer in review cannot get a change request either
    stale = decide(ella, content, "request_changes", comment="Too late", expected="approved")
    assert "not waiting for a decision" in stale.content.decode()
    assert statuses(content) == {"approved"}


def test_service_refuses_changes_on_draft_and_archived_client(workspace, team, ella, content):
    with pytest.raises(Exception, match="not waiting for a decision"):
        request_content_changes(content, ella, "Change", expected_stage="idea")
    set_mode(workspace, SINGLE)
    to_review(content, team)
    Workspace.objects.filter(pk=workspace.pk).update(is_archived=True)
    from django.core.exceptions import PermissionDenied

    with pytest.raises(PermissionDenied):
        request_content_changes(content, ella, "Change", expected_stage="review")


# ---- AK3: history who and when, visibility by the actor's role at that time --------------------


def test_history_shows_who_when_and_respects_note_visibility(workspace, team, manager, ella, content):
    set_mode(workspace, SINGLE)
    to_review(content, team)
    decide(manager, content, "request_changes", comment="TEAM NOTE: legal wants a disclaimer.")
    move_content(content, "review", team, expected_stage="review")
    decide(ella, content, "request_changes", comment="CLIENT NOTE: lighter music.")

    client_history = login(ella).get(detail_url(content, "history")).content.decode()
    team_history = login(team).get(detail_url(content, "history")).content.decode()
    assert "CLIENT NOTE: lighter music." in client_history and "CLIENT NOTE: lighter music." in team_history
    assert "TEAM NOTE" not in client_history and "TEAM NOTE" in team_history
    assert "Ella" in client_history and "Mia Manager" in team_history
    # the card repeats the latest decision with the same visibility rule
    card = login(ella).get(detail_url(content)).content.decode()
    assert "Latest decision" in card and "CLIENT NOTE: lighter music." in card and "TEAM NOTE" not in card

    # a later change of the author's role does not change what was recorded
    join(workspace, ella, "manager")
    join(workspace, manager, "client")
    assert "TEAM NOTE" not in login(manager).get(detail_url(content, "history")).content.decode()


# ---- AK4: "Waiting for you" --------------------------------------------------------------------


def board_html(person, **query):
    return login(person).get(reverse("jrc_production:board"), query).content.decode()


def test_waiting_for_you_lists_only_own_client_content_for_clients(workspace, team, ella, viewer, manager, content):
    set_mode(workspace, SINGLE)
    other_org = Organization.objects.create(name="Other org")
    other_ws = Workspace.objects.create(organization=other_org, name="Other client", approval_workflow_mode=SINGLE)
    join(other_ws, team, "owner")
    create_manual_channel(other_ws, "instagram", "Other IG")
    foreign = create_content(other_ws, "Foreign reel", team)
    to_review(foreign, team)
    to_review(content, team)

    html = board_html(ella)
    assert "Waiting for you" in html and "Ella garden reel" in html
    assert "Foreign reel" not in html.split("Waiting for you")[1].split("</section>")[0]
    assert "?tab=approval" in html
    # the team, a viewer and a client with nothing pending see no such list
    for person in (team, manager, viewer):
        assert "data-waiting-for-you" not in board_html(person)
    decide(ella, content, "approve")
    assert "data-waiting-for-you" not in board_html(ella)


def test_waiting_for_you_follows_the_two_stage_order(workspace, team, ella, content):
    set_mode(workspace, TWO_STAGE)
    to_review(content, team)
    assert "data-waiting-for-you" not in board_html(ella)
    decide(team, content, "approve")
    assert "data-waiting-for-you" in board_html(ella)
    decide(ella, content, "request_changes", comment="Needs work.")
    assert "data-waiting-for-you" not in board_html(ella)
