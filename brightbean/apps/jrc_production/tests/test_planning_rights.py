# JR Content: přidáno 2026-11-06 (blok P1-B32), AGPL-3.0.
"""Who may plan content, on every planning path; review only where the mode has one (P1-B32).

Expectations are written by hand, not derived from ``apps.members.permissions``:

* Planning (set a date, schedule, queue, publish now, re-time a scheduled copy) needs publishing
  rights: owner and manager. Editor, contributor, client and viewer never plan, on any path.
* What may go out follows the approval mode (P1-B27): a draft only in "No approvals" and
  "Optional approvals", an approved copy in every mode, a copy on hold / sent back / rejected never.
* "No approvals" has no review step: nothing offers it and nothing accepts it (PM decision 6. 11.).
"""

import csv
import io
import json
import pathlib
import re
from datetime import time, timedelta

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.api_keys.services import issue_api_key
from apps.approvals import services as approvals
from apps.calendar.models import PostingSlot, Queue
from apps.composer.models import PlatformPost, Post
from apps.jrc_channels.services import create_manual_channel
from apps.jrc_production.services import available_moves
from apps.members.models import CustomRole, OrgMembership, WorkspaceMembership
from apps.members.permissions import can_schedule
from apps.workspaces.models import Workspace

pytestmark = pytest.mark.django_db

MODES = ["none", "optional", "required_internal", "required_internal_and_client"]
ROLES = ["owner", "manager", "editor", "contributor", "client", "viewer"]
DIRECT = {"none", "optional"}  # a draft may go out without approval
PLANNERS = {"owner", "manager"}  # publishing rights


@pytest.fixture
def setup(organization):
    def build(mode, role):
        workspace = Workspace.objects.create(
            organization=organization, name=f"Plan {mode} {role}", approval_workflow_mode=mode
        )
        # Custom channel: manual (the board may plan it) and publishable without media.
        account = create_manual_channel(workspace, "custom", "Plan channel")
        for day in range(7):
            PostingSlot.objects.create(social_account=account, day_of_week=day, time=time(9, 0))
        Queue.objects.create(workspace=workspace, name="Plan queue", social_account=account)
        owner = User.objects.create_user(email=f"owner-{mode}-{role}@example.invalid", tos_accepted_at=timezone.now())
        OrgMembership.objects.create(user=owner, organization=organization, org_role="owner")
        WorkspaceMembership.objects.create(user=owner, workspace=workspace, workspace_role="owner")
        actor = owner
        if role != "owner":
            actor = User.objects.create_user(email=f"{role}-{mode}@example.invalid", tos_accepted_at=timezone.now())
            OrgMembership.objects.create(user=actor, organization=organization, org_role="member")
            WorkspaceMembership.objects.create(user=actor, workspace=workspace, workspace_role=role)
        return workspace, account, owner, actor

    return build


def _content(workspace, account, author, status, title):
    """The actor's own content, so authorship is never the reason for a refusal."""
    post = Post.objects.create(workspace=workspace, author=author, title=title, caption="Ready caption")
    variant = PlatformPost.objects.create(post=post, social_account=account, status=status)
    if status == "scheduled":
        variant.scheduled_at = timezone.now() + timedelta(days=5)
        variant.save(update_fields=["scheduled_at"])
        post.scheduled_at = variant.scheduled_at
        post.save(update_fields=["scheduled_at"])
    return post, variant


def _key_client(workspace, account, actor, organization):
    """REST/MCP client for a key with everything the actor holds (None when the role holds nothing)."""
    membership = WorkspaceMembership.objects.get(workspace=workspace, user=actor)
    permissions = [k for k, v in membership.effective_permissions.items() if v]
    org = OrgMembership.objects.get(user=actor, organization=organization)
    role = org.org_role
    org.org_role = "admin"  # only org admins may issue keys; the workspace role stays the actor's
    org.save(update_fields=["org_role"])
    try:
        key = issue_api_key(
            workspace=workspace, social_accounts=[account], issued_by=actor, name="plan", permissions=permissions
        )
    finally:
        org.org_role = role
        org.save(update_fields=["org_role"])
    return Client(HTTP_AUTHORIZATION=f"Bearer {key.plaintext_token}")


def _mcp(api, name, arguments):
    rpc = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": arguments}}
    response = api.post("/api/v1/mcp/", data=json.dumps(rpc), content_type="application/json", secure=True)
    envelope = response.json()
    ok = "error" not in envelope and not envelope.get("result", {}).get("isError", False)
    return ok


def _composer(client, workspace, post, account, action, when=None):
    data = {"action": action, "title": post.title, "caption": post.caption, "selected_accounts": str(account.pk)}
    if when is not None:
        data.update({"scheduled_date": when.strftime("%Y-%m-%d"), "scheduled_time": when.strftime("%H:%M")})
    return client.post(f"/workspace/{workspace.pk}/compose/{post.pk}/save/", data, HTTP_HX_REQUEST="true")


def _csv_import(client, workspace, when):
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["date", "time", "platform", "caption"])
    writer.writerow([when.strftime("%Y-%m-%d"), when.strftime("%H:%M"), "custom", "Imported with a date"])
    upload = io.BytesIO(buffer.getvalue().encode())
    upload.name = "plan.csv"
    base = f"/workspace/{workspace.pk}/import/csv"
    first = client.post(f"{base}/", {"csv_file": upload})
    if first.status_code != 200:
        return first, None
    # P1-B34: the whole flow, the preview step stores the mapping.
    preview = client.post(
        f"{base}/preview/", {"map_date": "0", "map_time": "1", "map_platforms": "2", "map_caption": "3"}
    )
    assert preview.status_code == 200
    response = client.post(f"{base}/confirm/")
    variant = PlatformPost.objects.filter(post__workspace=workspace, post__caption="Imported with a date").first()
    return response, variant


@pytest.mark.parametrize("role", ROLES)
@pytest.mark.parametrize("mode", MODES)
def test_every_planning_path_needs_publishing_rights_and_the_approval_mode(client, setup, organization, mode, role):
    workspace, account, _owner, actor = setup(mode, role)
    client.force_login(actor)
    when = timezone.localtime(timezone.now()) + timedelta(days=2)
    stamp = when.strftime("%Y-%m-%dT%H:%M")
    api = _key_client(workspace, account, actor, organization)
    results = {}  # path -> (refused by the server, went out)

    for state in ("draft", "approved"):

        def content(name, state=state):
            return _content(workspace, account, actor, state, f"{state} {name}")

        post, variant = content("set date")
        response = client.post(
            reverse("jrc_production:content_detail", kwargs={"workspace_id": workspace.pk, "post_id": post.pk}),
            {"action": "set_date", "platform_post": variant.pk, "scheduled_at": stamp},
        )
        results[(state, "JR detail Set date")] = (response, variant)

        post, variant = content("board")
        expected_stage = "idea" if state == "draft" else "approved"
        response = client.post(
            reverse("jrc_production:move", kwargs={"workspace_id": workspace.pk, "post_id": post.pk}),
            {"stage": "ready", "expected_stage": expected_stage, "scheduled_at": stamp},
        )
        results[(state, "board Ready")] = (response, variant)

        for action in ("schedule", "publish_now", "add_to_queue", "add_to_queue_priority"):
            post, variant = content(action)
            response = _composer(client, workspace, post, account, action, when if action == "schedule" else None)
            results[(state, f"composer {action}")] = (response, variant)

        post, variant = content("chip")
        response = client.post(
            f"/workspace/{workspace.pk}/compose/{post.pk}/platform-posts/{variant.pk}/transition/",
            {"target_status": "scheduled"},
            HTTP_HX_REQUEST="true",
        )
        results[(state, "composer chip")] = (response, variant)

        post, variant = content("bulk")
        response = client.post(
            f"/workspace/{workspace.pk}/calendar/bulk-action/",
            {"action": "publish", "platform_post_ids": str(variant.pk)},
            HTTP_HX_REQUEST="true",
        )
        results[(state, "calendar bulk Publish")] = (response, variant)

        post, variant = content("rest schedule")
        response = api.post(
            f"/api/v1/posts/{post.pk}/schedule",
            data=json.dumps({"scheduled_at": when.isoformat()}),
            content_type="application/json",
            secure=True,
        )
        results[(state, "REST /schedule")] = (response, variant)

        post, variant = content("rest stage")
        response = api.post(
            f"/api/v1/posts/{post.pk}/stage",
            data=json.dumps({"stage": "ready", "expected_stage": expected_stage, "scheduled_at": when.isoformat()}),
            content_type="application/json",
            secure=True,
        )
        results[(state, "REST stage Ready")] = (response, variant)

        post, variant = content("mcp schedule_draft")
        ok = _mcp(api, "schedule_draft", {"post_id": str(post.pk), "scheduled_at": when.isoformat()})
        results[(state, "MCP schedule_draft")] = (ok, variant)

    # Paths that create planned content, or plan a draft only.
    post, variant = _content(workspace, account, actor, "draft", "calendar drag")
    response = client.post(
        f"/workspace/{workspace.pk}/calendar/reschedule/",
        {"platform_post_id": str(variant.pk), "new_datetime": stamp},
        HTTP_HX_REQUEST="true",
    )
    results[("draft", "calendar drag")] = (response, variant)

    response, variant = _csv_import(client, workspace, when)
    results[("draft", "CSV import with a date")] = (response, variant)

    before = set(Post.objects.filter(workspace=workspace).values_list("pk", flat=True))
    response = api.post(
        "/api/v1/posts/",
        data=json.dumps(
            {
                "social_account_id": str(account.pk),
                "caption": "x",
                "action": "schedule",
                "scheduled_at": when.isoformat(),
            }
        ),
        content_type="application/json",
        secure=True,
    )
    created = PlatformPost.objects.filter(post__workspace=workspace).exclude(post__pk__in=before).first()
    results[("draft", "REST create scheduled")] = (response, created)

    before = set(Post.objects.filter(workspace=workspace).values_list("pk", flat=True))
    ok = _mcp(
        api, "schedule_post", {"social_account_id": str(account.pk), "caption": "x", "scheduled_at": when.isoformat()}
    )
    created = PlatformPost.objects.filter(post__workspace=workspace).exclude(post__pk__in=before).first()
    results[("draft", "MCP schedule_post")] = (ok, created)

    for (state, path), (response, variant) in results.items():
        goes_out = role in PLANNERS and (state == "approved" or mode in DIRECT)
        if variant is not None:
            variant.refresh_from_db()
        went_out = variant is not None and variant.status == "scheduled"
        assert went_out is goes_out, (state, path)
        if isinstance(response, bool):
            assert response is goes_out, (state, path)
        elif path == "CSV import with a date" and response.status_code == 200:
            # The import itself is allowed to every content creator; a date is planned only with rights.
            assert variant is not None and variant.status == ("scheduled" if goes_out else "draft"), path
        elif path == "JR detail Set date" and role in PLANNERS and not goes_out:
            # The detail page answers with the reason next to the form (P1-B27).
            assert response.status_code == 200 and b"needs approval" in response.content, (state, path)
        elif goes_out:
            assert response.status_code < 400, (state, path, response.status_code, response.content[:300])
        else:
            assert response.status_code >= 400, (state, path, response.status_code)


@pytest.mark.parametrize("role", ROLES)
def test_moving_a_planned_date_is_planning_too(client, setup, organization, role):
    """Re-timing a scheduled copy needs the same right on every path (calendar drag, composer, JR, REST)."""
    workspace, account, _owner, actor = setup("required_internal_and_client", role)
    client.force_login(actor)
    api = _key_client(workspace, account, actor, organization)
    when = timezone.localtime(timezone.now()) + timedelta(days=9)
    stamp = when.strftime("%Y-%m-%dT%H:%M")
    attempts = {}

    post, variant = _content(workspace, account, actor, "scheduled", "drag")
    attempts["calendar drag"] = (
        client.post(
            f"/workspace/{workspace.pk}/calendar/reschedule/",
            {"platform_post_id": str(variant.pk), "new_datetime": stamp},
            HTTP_HX_REQUEST="true",
        ),
        variant,
    )
    post, variant = _content(workspace, account, actor, "scheduled", "composer")
    attempts["composer schedule"] = (_composer(client, workspace, post, account, "schedule", when), variant)
    post, variant = _content(workspace, account, actor, "scheduled", "detail")
    attempts["JR detail Set date"] = (
        client.post(
            reverse("jrc_production:content_detail", kwargs={"workspace_id": workspace.pk, "post_id": post.pk}),
            {"action": "set_date", "platform_post": variant.pk, "scheduled_at": stamp},
        ),
        variant,
    )
    post, variant = _content(workspace, account, actor, "scheduled", "patch")
    attempts["REST PATCH scheduled_at"] = (
        api.patch(
            f"/api/v1/posts/{post.pk}",
            data=json.dumps({"scheduled_at": when.isoformat()}),
            content_type="application/json",
            secure=True,
        ),
        variant,
    )
    for path, (response, variant) in attempts.items():
        original = variant.scheduled_at
        variant.refresh_from_db()
        moved = abs((variant.scheduled_at - when).total_seconds()) < 60
        assert moved is (role in PLANNERS), (path, response.status_code)
        if role not in PLANNERS:
            assert response.status_code >= 400 and variant.scheduled_at == original, path
            assert variant.status == "scheduled", path


def test_a_viewer_never_plans_even_with_a_custom_role(setup, organization):
    workspace, _account, _owner, actor = setup("none", "viewer")
    role = CustomRole.objects.create(
        organization=organization, name="Viewer planner", permissions={"create_posts": True, "publish_directly": True}
    )
    membership = WorkspaceMembership.objects.get(workspace=workspace, user=actor)
    membership.custom_role = role
    membership.save(update_fields=["custom_role"])
    membership = WorkspaceMembership.objects.select_related("custom_role", "workspace", "user").get(pk=membership.pk)
    assert membership.effective_permissions["publish_directly"] is True
    assert can_schedule(membership) is False


@pytest.mark.parametrize("role", ROLES)
def test_composer_offers_planning_only_with_publishing_rights(client, setup, role):
    workspace, account, _owner, actor = setup("none", role)
    if role in {"client", "viewer"}:
        return  # no composer at all (covered by the write gate)
    client.force_login(actor)
    post, _variant = _content(workspace, account, actor, "draft", "page")
    html = client.get(f"/workspace/{workspace.pk}/compose/{post.pk}/").content.decode()
    offers = 'id="publish-action-btn"' in html and 'data-mode="publish_now"' in html
    assert offers is (role in PLANNERS)
    # Everyone who edits keeps the proposed-time panel that Save Draft stores.
    assert 'name="scheduled_date"' in html and 'name="scheduled_time"' in html
    assert ("data-propose-only-hint" in html) is (role not in PLANNERS)


def test_save_draft_without_rights_keeps_the_proposed_time_and_plans_nothing(client, setup):
    workspace, account, _owner, actor = setup("none", "editor")
    client.force_login(actor)
    post, variant = _content(workspace, account, actor, "draft", "proposal")
    when = timezone.localtime(timezone.now()) + timedelta(days=4)
    response = _composer(client, workspace, post, account, "save_draft", when)
    assert response.status_code < 400
    post.refresh_from_db()
    variant.refresh_from_db()
    assert post.proposed_publish_at is not None and variant.status == "draft" and post.scheduled_at is None


@pytest.mark.parametrize("role", ["owner", "editor"])
def test_calendar_offers_publish_only_with_publishing_rights(client, setup, role):
    workspace, account, _owner, actor = setup("none", role)
    client.force_login(actor)
    _content(workspace, account, actor, "scheduled", "queued")
    html = client.get(f"/workspace/{workspace.pk}/calendar/", {"mode": "list", "tab": "queue"}).content.decode()
    assert ("ask('publish')" in html) is (role in PLANNERS)
    assert ("action=publish_now" in html) is (role in PLANNERS)


def test_csv_import_says_why_dates_stayed_drafts(client, setup):
    workspace, _account, _owner, actor = setup("none", "editor")
    client.force_login(actor)
    response, variant = _csv_import(client, workspace, timezone.localtime(timezone.now()) + timedelta(days=3))
    assert response.status_code == 200 and variant.status == "draft"
    assert b"data-cannot-schedule" in response.content and b"data-approval-needed" not in response.content


# --- AK4: a copy on hold (or sent back) never goes out, also not through a hop via draft -------------------


@pytest.mark.parametrize("status", ["on_hold", "changes_requested", "rejected"])
@pytest.mark.parametrize("mode", MODES)
def test_composer_never_releases_held_or_sent_back_copies(client, setup, mode, status):
    workspace, account, owner, _actor = setup(mode, "owner")
    client.force_login(owner)
    when = timezone.localtime(timezone.now()) + timedelta(days=2)
    for action in ("schedule", "publish_now", "add_to_queue", "add_to_queue_priority"):
        post, variant = _content(workspace, account, owner, status, f"{status} {action}")
        response = _composer(client, workspace, post, account, action, when if action == "schedule" else None)
        variant.refresh_from_db()
        assert response.status_code == 400, (action, response.status_code)
        assert variant.status == status, action
        assert b"cannot be scheduled or published yet" in response.content, action


def test_children_transition_never_hops_through_draft_into_scheduled(setup):
    from apps.composer.views import _transition_post_children

    workspace, account, owner, _actor = setup("none", "owner")
    post, variant = _content(workspace, account, owner, "on_hold", "hop")
    moved, skipped = _transition_post_children(post, "scheduled")
    variant.refresh_from_db()
    assert variant.status == "on_hold" and moved == [] and [pp.pk for pp in skipped] == [variant.pk]


# --- AK3: no review step in "No approvals", one rule for offering and accepting -----------------------------


@pytest.mark.parametrize("mode", MODES)
def test_review_is_offered_and_accepted_only_where_the_mode_has_one(client, setup, organization, mode):
    workspace, account, owner, _actor = setup(mode, "owner")
    client.force_login(owner)
    has_review = mode != "none"
    membership = WorkspaceMembership.objects.get(workspace=workspace, user=owner)
    api = _key_client(workspace, account, owner, organization)

    # Board and JR detail (moves), REST and MCP stage: offered.
    for status in ("draft", "changes_requested", "rejected"):
        post, _variant = _content(workspace, account, owner, status, f"offer {status}")
        assert ("review" in available_moves(post, membership)) is has_review, status
        rest = api.get(f"/api/v1/posts/{post.pk}/stage", secure=True).json()
        assert ("review" in rest["available_moves"]) is has_review, status
    board = client.get(reverse("jrc_production:board"), {"client": workspace.pk}).content.decode()
    assert ('<option value="review">' in board) is has_review

    # Composer buttons.
    post, _variant = _content(workspace, account, owner, "draft", "composer draft")
    html = client.get(f"/workspace/{workspace.pk}/compose/{post.pk}/").content.decode()
    assert ('value="submit_for_approval"' in html) is has_review
    post, _variant = _content(workspace, account, owner, "changes_requested", "composer back")
    html = client.get(f"/workspace/{workspace.pk}/compose/{post.pk}/").content.decode()
    assert ('value="resubmit_for_approval"' in html) is has_review

    # Accepted: board move, REST stage, composer actions, chip, the approval service itself.
    outcomes = {}
    post, variant = _content(workspace, account, owner, "draft", "board review")
    outcomes["board"] = (
        client.post(
            reverse("jrc_production:move", kwargs={"workspace_id": workspace.pk, "post_id": post.pk}),
            {"stage": "review", "expected_stage": "idea"},
        ).status_code,
        variant,
    )
    post, variant = _content(workspace, account, owner, "draft", "rest review")
    outcomes["REST stage"] = (
        api.post(
            f"/api/v1/posts/{post.pk}/stage",
            data=json.dumps({"stage": "review", "expected_stage": "idea"}),
            content_type="application/json",
            secure=True,
        ).status_code,
        variant,
    )
    post, variant = _content(workspace, account, owner, "draft", "submit")
    outcomes["composer submit"] = (
        _composer(client, workspace, post, account, "submit_for_approval").status_code,
        variant,
    )
    post, variant = _content(workspace, account, owner, "changes_requested", "resubmit")
    outcomes["composer resubmit"] = (
        _composer(client, workspace, post, account, "resubmit_for_approval").status_code,
        variant,
    )
    post, variant = _content(workspace, account, owner, "draft", "chip")
    outcomes["chip"] = (
        client.post(
            f"/workspace/{workspace.pk}/compose/{post.pk}/platform-posts/{variant.pk}/transition/",
            {"target_status": "pending_review"},
            HTTP_HX_REQUEST="true",
        ).status_code,
        variant,
    )
    for path, (status_code, variant) in outcomes.items():
        before = variant.status
        variant.refresh_from_db()
        if has_review:
            assert status_code < 400 and variant.status == "pending_review", (path, status_code)
        else:
            assert status_code >= 400 and variant.status == before, (path, status_code)

    post, variant = _content(workspace, account, owner, "draft", "service")
    if has_review:
        approvals.submit_for_review(post, owner, workspace)
        variant.refresh_from_db()
        assert variant.status == "pending_review"
    else:
        with pytest.raises(ValueError, match="No approvals"):
            approvals.submit_for_review(post, owner, workspace)
        with pytest.raises(ValueError, match="No approvals"):
            approvals.resubmit_post(post, owner, workspace)


def test_planning_rights_are_read_in_one_place():
    """No path reads ``publish_directly`` itself: every planning check goes through ``can_schedule``."""
    root = pathlib.Path(__file__).resolve().parents[3]
    pattern = re.compile(r"""\.get\(\s*["']publish_directly["']""")
    readers = set()
    for path in (root / "apps").rglob("*.py"):
        relative = path.relative_to(root).as_posix()
        if "/tests/" in relative or "/migrations/" in relative or path.name.startswith("test"):
            continue
        if pattern.search(path.read_text(encoding="utf-8")):
            readers.add(relative)
    assert readers == {"apps/members/permissions.py"}


def test_every_release_site_checks_the_planning_right():
    """The places that move a copy out (registry of P1-B27) also check who plans, with the one rule."""
    from apps.jrc_production.tests.test_approval_modes import _REVIEWED_RELEASE_SITES

    root = pathlib.Path(__file__).resolve().parents[3]
    for name in _REVIEWED_RELEASE_SITES:
        assert "can_schedule(" in (root / name).read_text(encoding="utf-8"), name
    for name in ("apps/api/routers/posts.py", "apps/mcp/handlers.py", "apps/jrc_ui/write_access.py"):
        assert "can_schedule(" in (root / name).read_text(encoding="utf-8"), name
