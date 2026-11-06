# JR Content: přidáno 2026-11-06 (blok P1-B27), AGPL-3.0.
"""Approval workflow modes × roles × actions (P1-B27, E-08-ex6b-01/02).

The expectations below are written by hand from the descriptions on the Approvals settings page,
not derived from ``apps.members.permissions``:

* No approvals: "Anyone with publishing rights can schedule or publish directly."
* Optional approvals: "Creators can choose to submit a post for review, but it's not required."
* Internal review required: "Every post must be approved by a team member with review rights before it can go out."
* Internal review + client approval: "Posts go through internal review, then are sent to the client portal for
  final sign-off."

Roles: owner and manager review and schedule; the client marks content as published and downloads the final
files (JR rule since F5-B07) and decides only at the client step; editor, contributor and viewer do none of it.
"""

import json
import pathlib
import re
import uuid
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.test import Client, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.api_keys.services import issue_api_key
from apps.composer.models import PlatformPost, Post
from apps.composer.services import create_post, require_release_allowed, transition_platform_post
from apps.jrc_channels.services import create_manual_channel
from apps.jrc_production.models import ManualPublication
from apps.jrc_production.services import available_moves
from apps.jrc_video.models import VideoAsset, VideoVersion
from apps.members.models import OrgMembership, WorkspaceMembership
from apps.workspaces.models import Workspace

pytestmark = pytest.mark.django_db

MODES = ["none", "optional", "required_internal", "required_internal_and_client"]
ROLES = ["owner", "manager", "editor", "contributor", "client", "viewer"]
DIRECT = {"none", "optional"}  # a draft may go out without approval
SCHEDULERS = {"owner", "manager"}  # publish_directly
PUBLISHERS = {"owner", "manager", "client"}  # mark as published, download final files
TEAM_DECIDERS = {"owner", "manager"}


def decides(mode, role, step):
    if mode == "required_internal_and_client":
        return role == "client" if step == "pending_client" else role in TEAM_DECIDERS
    return role in TEAM_DECIDERS


@pytest.fixture
def setup(organization):
    def build(mode, role):
        workspace = Workspace.objects.create(
            organization=organization, name=f"Modes {mode}", approval_workflow_mode=mode
        )
        account = create_manual_channel(workspace, "instagram", "Modes channel")
        owner = User.objects.create_user(email=f"owner-{mode}@example.invalid", tos_accepted_at=timezone.now())
        OrgMembership.objects.create(user=owner, organization=organization, org_role="owner")
        WorkspaceMembership.objects.create(user=owner, workspace=workspace, workspace_role="owner")
        actor = owner
        if role != "owner":
            actor = User.objects.create_user(email=f"{role}-{mode}@example.invalid", tos_accepted_at=timezone.now())
            OrgMembership.objects.create(user=actor, organization=organization, org_role="member")
            WorkspaceMembership.objects.create(user=actor, workspace=workspace, workspace_role=role)

        def content(name, status, *, caption="Ready caption", video=False):
            post = Post.objects.create(workspace=workspace, author=owner, title=name, caption=caption)
            variant = PlatformPost.objects.create(post=post, social_account=account, status=status)
            if video:
                asset = VideoAsset.objects.create(platform_post=variant, ff_asset_id=uuid.uuid4())
                version = VideoVersion.objects.create(video=asset, number=1, ff_version_id=uuid.uuid4(), status="ready")
                asset.final_version = version
                asset.save(update_fields=["final_version"])
            return post, variant

        return workspace, actor, content

    return build


def _detail(workspace, post):
    return reverse("jrc_production:content_detail", kwargs={"workspace_id": workspace.pk, "post_id": post.pk})


@pytest.mark.parametrize("role", ROLES)
@pytest.mark.parametrize("mode", MODES)
def test_mode_role_action_matrix(client, setup, mode, role):
    workspace, actor, content = setup(mode, role)
    client.force_login(actor)
    future = (timezone.localtime(timezone.now()) + timedelta(days=3)).strftime("%Y-%m-%dT%H:%M")

    # approve / request changes at every review step of the mode
    steps = ["pending_review"] + (["pending_client"] if mode == "required_internal_and_client" else [])
    for step in steps:
        for decision in ("approve", "request_changes"):
            post, variant = content(f"{step} {decision}", step)
            url = reverse("jrc_production:approval_decision", kwargs={"workspace_id": workspace.pk, "post_id": post.pk})
            response = client.post(url, {"decision": decision, "expected_stage": "review", "comment": "Please fix"})
            variant.refresh_from_db()
            if decides(mode, role, step):
                assert response.status_code == 302, (step, decision)
                assert variant.status != step, (step, decision)
            else:
                assert response.status_code == 403, (step, decision)
                assert variant.status == step, (step, decision)

    for state in ("draft", "approved"):
        goes_out = state == "approved" or mode in DIRECT

        # set date
        post, variant = content(f"{state} date", state)
        response = client.post(
            _detail(workspace, post), {"action": "set_date", "platform_post": variant.pk, "scheduled_at": future}
        )
        variant.refresh_from_db()
        if role not in SCHEDULERS:
            assert response.status_code == 403, state
        elif goes_out:
            assert response.status_code == 302, state
        else:
            assert response.status_code == 200 and b"needs approval" in response.content, state
        expected = "scheduled" if role in SCHEDULERS and goes_out else state
        assert variant.status == expected, ("set date", state)

        # mark published
        post, variant = content(f"{state} mark", state)
        url = reverse("jrc_production:mark_published", kwargs={"workspace_id": workspace.pk, "post_id": post.pk})
        response = client.post(url, {"platform_post": variant.pk})
        variant.refresh_from_db()
        published = role in PUBLISHERS and goes_out
        assert response.status_code == (302 if role in PUBLISHERS else 403), ("mark", state)
        assert ManualPublication.objects.filter(platform_post=variant).exists() is published, ("mark", state)
        assert variant.status == ("published" if published else state), ("mark", state)

        # download the final video
        post, variant = content(f"{state} download", state, video=True)
        url = reverse(
            "jrc_video:final_download",
            kwargs={"workspace_id": workspace.pk, "post_id": post.pk, "platform_post_id": variant.pk},
        )
        signed = "http://testserver/files/video/raw/file.mp4?filename=final.mp4"
        with (
            override_settings(JRC_S3_PUBLIC_ORIGIN="http://127.0.0.1:14133"),
            patch("apps.jrc_video.uploads._call", return_value={"url": signed}) as call,
        ):
            response = client.get(url)
        if role not in PUBLISHERS:
            assert response.status_code == 403, ("download", state)
        else:
            assert response.status_code == (302 if goes_out else 404), ("download", state)
        assert call.called is (role in PUBLISHERS and goes_out), ("download", state)


@pytest.mark.parametrize("mode", MODES)
def test_publish_tab_offers_what_the_server_allows_and_says_why(client, setup, mode):
    workspace, owner, content = setup(mode, "owner")
    client.force_login(owner)
    post, _variant = content("draft", "draft", video=True)
    html = client.get(_detail(workspace, post), {"tab": "publish"}).content.decode()
    if mode in DIRECT:
        assert ">Set date<" in html and "Mark as published" in html and "Download final video" in html
        assert "needs approval" not in html
    else:
        assert ">Set date<" not in html and "Mark as published" not in html and "Download final video" not in html
        need = "the team and then the client" if mode == "required_internal_and_client" else "the team first"
        assert f"No date can be set yet. It needs approval by {need}" in html
        assert f"To mark this channel as published: It needs approval by {need}" in html
        assert f"To download the final video: It needs approval by {need}" in html
    assert "after approval" not in html


@pytest.mark.parametrize("mode", MODES)
def test_board_rest_and_mcp_offer_ready_from_a_draft_only_without_required_approval(setup, mode):
    workspace, owner, content = setup(mode, "owner")
    post, _variant = content("board", "draft")
    approved, _ = content("approved", "approved")
    membership = WorkspaceMembership.objects.get(workspace=workspace, user=owner)
    assert ("ready" in available_moves(post, membership)) is (mode in DIRECT)
    assert "ready" in available_moves(approved, membership)
    key = issue_api_key(
        workspace=workspace,
        social_accounts=list(workspace.social_accounts.all()),
        issued_by=owner,
        name="modes",
        permissions=[k for k, v in membership.effective_permissions.items() if v],
    )
    api = Client(HTTP_AUTHORIZATION=f"Bearer {key.plaintext_token}")
    rest = api.get(f"/api/v1/posts/{post.pk}/stage", secure=True).json()
    assert ("ready" in rest["available_moves"]) is (mode in DIRECT)
    rpc = {"jsonrpc": "2.0", "id": 1, "method": "tools/call"}
    rpc["params"] = {"name": "jrc_get_stage", "arguments": {"post_id": str(post.pk)}}
    envelope = api.post("/api/v1/mcp/", data=json.dumps(rpc), content_type="application/json", secure=True).json()
    mcp = json.loads(envelope["result"]["content"][0]["text"])
    assert mcp["available_moves"] == rest["available_moves"]


@pytest.mark.parametrize("mode", MODES)
def test_board_move_to_ready_from_draft(client, setup, mode):
    workspace, owner, content = setup(mode, "owner")
    client.force_login(owner)
    post, variant = content("board", "draft")
    when = (timezone.localtime(timezone.now()) + timedelta(days=2)).strftime("%Y-%m-%dT%H:%M")
    response = client.post(
        reverse("jrc_production:move", kwargs={"workspace_id": workspace.pk, "post_id": post.pk}),
        {"stage": "ready", "expected_stage": "idea", "scheduled_at": when},
    )
    variant.refresh_from_db()
    if mode in DIRECT:
        assert response.status_code == 302 and variant.status == "scheduled"
    else:
        assert response.status_code == 403 and variant.status == "draft"


@pytest.mark.parametrize("mode", MODES)
def test_upstream_schedule_paths_follow_the_mode(client, setup, mode):
    """Composer schedule / publish now / chip, calendar drag and bulk publish, service and CSV-free API."""
    workspace, owner, content = setup(mode, "owner")
    account = workspace.social_accounts.get()
    custom = create_manual_channel(workspace, "custom", "Custom")  # publishable without media
    client.force_login(owner)
    when = timezone.localtime(timezone.now()) + timedelta(days=2)
    base = f"/workspace/{workspace.pk}"

    def draft(title):
        post = Post.objects.create(workspace=workspace, author=owner, title=title, caption="x")
        return post, PlatformPost.objects.create(post=post, social_account=custom, status="draft")

    results = {}
    post, pp = draft("composer schedule")
    results["composer schedule"] = (
        client.post(
            f"{base}/compose/{post.pk}/save/",
            {
                "action": "schedule",
                "title": post.title,
                "caption": "x",
                "selected_accounts": str(custom.pk),
                "scheduled_date": when.strftime("%Y-%m-%d"),
                "scheduled_time": when.strftime("%H:%M"),
            },
            HTTP_HX_REQUEST="true",
        ),
        pp,
    )
    post, pp = draft("composer publish now")
    results["composer publish now"] = (
        client.post(
            f"{base}/compose/{post.pk}/save/",
            {"action": "publish_now", "title": post.title, "caption": "x", "selected_accounts": str(custom.pk)},
            HTTP_HX_REQUEST="true",
        ),
        pp,
    )
    post, pp = draft("chip")
    results["chip"] = (
        client.post(
            f"{base}/compose/{post.pk}/platform-posts/{pp.pk}/transition/",
            {"target_status": "scheduled"},
            HTTP_HX_REQUEST="true",
        ),
        pp,
    )
    post, pp = draft("calendar drag")
    results["calendar drag"] = (
        client.post(
            f"{base}/calendar/reschedule/",
            {"platform_post_id": str(pp.pk), "new_datetime": when.strftime("%Y-%m-%dT%H:%M")},
            HTTP_HX_REQUEST="true",
        ),
        pp,
    )
    post, pp = draft("calendar bulk")
    results["calendar bulk"] = (
        client.post(
            f"{base}/calendar/bulk-action/",
            {"action": "publish", "platform_post_ids": str(pp.pk)},
            HTTP_HX_REQUEST="true",
        ),
        pp,
    )
    for name, (response, pp) in results.items():
        pp.refresh_from_db()
        if mode in DIRECT:
            assert response.status_code < 400, (name, response.status_code, response.content[:300])
            assert pp.status == "scheduled", name
        else:
            assert response.status_code == 400, (name, response.status_code)
            assert b"approval" in response.content, name
            assert pp.status == "draft", name

    # Service layer used by REST and MCP: drafts only where allowed, approved copies in every mode.
    _post, pp = draft("service draft")
    approved = PlatformPost.objects.create(post=_post, social_account=account, status="approved")
    if mode in DIRECT:
        transition_platform_post(pp, "scheduled", scheduled_at=when)
        create_post(workspace=workspace, social_account=custom, caption="x", status="scheduled", scheduled_at=when)
    else:
        with pytest.raises(ValueError, match="approval"):
            transition_platform_post(pp, "scheduled", scheduled_at=when)
        with pytest.raises(ValueError, match="approval"):
            create_post(workspace=workspace, social_account=custom, caption="x", status="scheduled", scheduled_at=when)
    require_release_allowed(workspace, approved.status)


@pytest.mark.parametrize("status", ["pending_review", "pending_client", "changes_requested", "rejected", "on_hold"])
@pytest.mark.parametrize("mode", MODES)
def test_nothing_goes_out_while_waiting_or_sent_back(setup, mode, status):
    workspace, _owner, _content = setup(mode, "owner")
    with pytest.raises(ValueError):
        require_release_allowed(workspace, status)


@pytest.mark.parametrize("mode", MODES)
def test_approval_tab_status_does_not_claim_an_approval_that_was_not_required(client, setup, mode):
    workspace, owner, content = setup(mode, "owner")
    client.force_login(owner)
    post, _ = content("planned", "scheduled")
    html = client.get(_detail(workspace, post), {"tab": "approval"}).content.decode()
    if mode in DIRECT:
        assert "Planned for publication." in html and "Approved and planned" not in html
    else:
        assert "Approved and planned for publication." in html


# Every place that moves a channel copy out (to scheduled / publishing, or creates it scheduled) has been reviewed
# for the approval workflow and uses the shared rule in the same file. A new such place fails this test until it
# is reviewed and listed here (P1-B27). Dynamic targets (composer chip, ``transition_platform_post``) are covered
# by ``test_upstream_schedule_paths_follow_the_mode``.
_GOES_OUT = re.compile(
    r"(?<!can_)transition_to\((\"|')?(PlatformPost\.Status\.)?(scheduled|publishing|SCHEDULED|PUBLISHING)"
    r"|_transition_post_children\([^)]*\"scheduled\""
    r"|pending_target = \"scheduled\""
    r"|initial_(pp_)?status = .*\"scheduled\""
)
_REVIEWED_RELEASE_SITES = {
    # file: (number of sites, the shared rule the file uses)
    "apps/calendar/views.py": (2, "require_release_allowed"),  # drag a draft, bulk "Publish"
    "apps/composer/views.py": (7, "_validate_approval_release"),  # schedule, publish now, queue ×2, CSV import
    "apps/jrc_production/services.py": (3, "release_blocker"),  # board Ready, Set date, Mark as published
}


def test_every_release_site_is_reviewed_for_the_approval_workflow():
    root = pathlib.Path(__file__).resolve().parents[3]
    found = {}
    for path in (root / "apps").rglob("*.py"):
        relative = path.relative_to(root).as_posix()
        if (
            "/tests/" in relative
            or "/migrations/" in relative
            or path.name.startswith("test")
            or path.name == "tests.py"
        ):
            continue
        source = path.read_text(encoding="utf-8")
        count = len(_GOES_OUT.findall(source))
        if count:
            found[relative] = (count, source)
    assert {name: count for name, (count, _) in found.items()} == {
        name: count for name, (count, _) in _REVIEWED_RELEASE_SITES.items()
    }
    for name, (_count, rule) in _REVIEWED_RELEASE_SITES.items():
        assert rule in found[name][1], name
