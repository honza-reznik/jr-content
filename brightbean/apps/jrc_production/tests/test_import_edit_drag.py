# JR Content: přidáno 2026-11-07 (blok P1-B34), AGPL-3.0.
"""CSV import as a whole flow, editing approved content per approval mode, calendar drag per planning right (P1-B34).

Expectations are written by hand, not derived from ``apps.members.permissions``:

* CSV: upload → preview → confirm works; the preview lists exactly the rows the import skips.
* Editing approved content sends it back to review only in the modes that require approval
  ("Internal review required" and the two-stage mode); in "No approvals" and "Optional approvals" it
  stays approved.
* A calendar drop changes a copy's planned time in every status, so only owner and manager (publishing
  rights) may do it; a draft only where the mode lets it go out without approval. The chip is offered
  as draggable exactly when the drop is accepted.
"""

import re
import zoneinfo
from datetime import datetime, timedelta

import pytest
from django.test import Client
from django.utils import timezone

from apps.accounts.models import User
from apps.approvals.models import ApprovalAction
from apps.composer import views as composer_views
from apps.composer.models import PlatformPost, Post
from apps.jrc_channels.services import create_manual_channel, set_channel_enabled
from apps.members.models import OrgMembership, WorkspaceMembership
from apps.workspaces.models import Workspace

pytestmark = pytest.mark.django_db

MODES = ["none", "optional", "required_internal", "required_internal_and_client"]
ROLES = ["owner", "manager", "editor", "contributor", "client", "viewer"]


@pytest.fixture
def setup(organization):
    def build(mode, role="owner"):
        workspace = Workspace.objects.create(
            organization=organization, name=f"B34 {mode} {role}", approval_workflow_mode=mode
        )
        account = create_manual_channel(workspace, "custom", "B34 channel")
        actor = User.objects.create_user(email=f"{role}-{mode}@example.invalid", tos_accepted_at=timezone.now())
        OrgMembership.objects.create(
            user=actor, organization=organization, org_role="owner" if role == "owner" else "member"
        )
        WorkspaceMembership.objects.create(user=actor, workspace=workspace, workspace_role=role)
        client = Client()
        client.force_login(actor)
        return workspace, account, actor, client

    return build


# ---------------------------------------------------------------------------------------------- CSV import (AK1)

CSV_DAY = (timezone.localdate() + timedelta(days=10)).isoformat()
CSV = (
    "date,time,platform,caption,tags\n"
    f'{CSV_DAY},10:30,custom,B34 one,"x,y"\n'
    f"{CSV_DAY},11:00,Custom,B34 two,\n"
    f"{CSV_DAY},25:99,custom,B34 bad time,\n"
    f"{CSV_DAY},12:00,snapchat,B34 bad platform,\n"
    f"{CSV_DAY},12:30,instagram,B34 not connected,\n"
    f"{CSV_DAY},13:00,custom,,\n"
    f",13:30,custom,B34 no date,\n"
    f"07/11/2026,14:00,custom,B34 bad date,\n"
)


def _upload(client, workspace, text=CSV):
    from django.core.files.uploadedfile import SimpleUploadedFile

    return client.post(
        f"/workspace/{workspace.pk}/import/csv/",
        {"csv_file": SimpleUploadedFile("b34.csv", text.encode(), content_type="text/csv")},
    )


def _preview(client, workspace):
    return client.post(
        f"/workspace/{workspace.pk}/import/csv/preview/",
        {"map_date": "0", "map_time": "1", "map_platforms": "2", "map_caption": "3", "map_tags": "4"},
    )


def _confirm(client, workspace):
    return client.post(f"/workspace/{workspace.pk}/import/csv/confirm/")


def _imported(workspace):
    return {
        post.caption: sorted(post.platform_posts.values_list("status", flat=True))
        for post in Post.objects.filter(workspace=workspace, caption__startswith="B34")
    }


@pytest.mark.parametrize(
    ("mode", "role", "copy_status", "hint"),
    [
        ("none", "owner", "scheduled", None),
        ("optional", "manager", "scheduled", None),
        ("required_internal", "owner", "draft", "needs approval by the team before"),
        ("required_internal_and_client", "owner", "draft", "needs approval by the team and then the client before"),
        ("none", "editor", "draft", "scheduling needs publishing rights"),
        ("required_internal", "contributor", "draft", "scheduling needs publishing rights"),
    ],
)
def test_csv_import_whole_flow(setup, mode, role, copy_status, hint):
    workspace, _account, actor, client = setup(mode, role)

    upload = _upload(client, workspace)
    assert upload.status_code == 200
    # Columns are recognised by their names (the page selects them on load).
    assert re.search(
        r'"date": 0.*"time": 1.*"platforms": 2.*"caption": 3.*"tags": 4', upload.content.decode().replace("&quot;", '"')
    )

    preview = _preview(client, workspace)
    assert preview.status_code == 200
    page = preview.content.decode()
    assert "2 valid" in page
    assert "6 errors" in page
    for row, problem in [
        (4, "Invalid time format &#x27;25:99&#x27; (expected HH:MM)"),
        (5, "Unknown platform &#x27;snapchat&#x27;"),
        (6, "Platform &#x27;instagram&#x27; is not connected"),
        (7, "Caption is empty"),
        (8, "Date is empty"),
        (9, "Invalid date format &#x27;07/11/2026&#x27; (expected YYYY-MM-DD)"),
    ]:
        assert re.search(rf"Row {row}</span>\s*<div[^>]*>\s*<span>{re.escape(problem)}", page), (row, problem)
    assert "6 invalid rows will be skipped." in page

    done = _confirm(client, workspace)
    assert done.status_code == 200
    result = re.sub(r"\s+", " ", done.content.decode())
    assert '2</span> <span class="text-xs text-green-600 block">posts created' in result
    assert '6</span> <span class="text-xs text-red-600 block">rows skipped' in result
    # Exactly the valid rows exist, each with its channel copy; no post without its channels.
    assert _imported(workspace) == {"B34 one": [copy_status], "B34 two": [copy_status]}
    one = Post.objects.get(workspace=workspace, caption="B34 one")
    assert one.author == actor and one.tags == ["x", "y"]
    expected = datetime.fromisoformat(f"{CSV_DAY}T10:30").replace(
        tzinfo=zoneinfo.ZoneInfo(workspace.effective_timezone or "UTC")
    )
    assert one.platform_posts.get().scheduled_at == expected
    if hint:
        assert hint in result
    else:
        assert "data-approval-needed" not in result and "data-cannot-schedule" not in result
    # The session holds nothing for a second confirm.
    assert _confirm(client, workspace).status_code == 400


def test_csv_preview_without_upload_asks_to_upload_again(setup):
    workspace, _account, _actor, client = setup("none")
    response = _preview(client, workspace)
    assert response.status_code == 400
    assert b"Please upload again" in response.content


def test_csv_disabled_channel_is_not_connected(setup):
    workspace, account, _actor, client = setup("none")
    set_channel_enabled(account, enabled=False)
    _upload(client, workspace, f"date,time,platform,caption\n{CSV_DAY},10:00,custom,B34 disabled\n")
    page = _preview(client, workspace).content.decode()
    assert "Platform &#x27;custom&#x27; is not connected" in page
    assert "No valid rows to import." in page
    _confirm(client, workspace)
    assert _imported(workspace) == {}


def test_csv_row_failing_half_way_leaves_nothing(setup, monkeypatch):
    """One savepoint per row: a failure after the post is saved removes the post too."""
    workspace, _account, _actor, client = setup("none")
    _upload(client, workspace, f"date,time,platform,caption\n{CSV_DAY},10:00,custom,B34 boom\n")
    assert _preview(client, workspace).status_code == 200

    def boom(*args, **kwargs):
        raise RuntimeError("channel check failed")

    monkeypatch.setattr(composer_views, "publish_blocker", boom)
    done = re.sub(r"\s+", " ", _confirm(client, workspace).content.decode())
    assert "No posts were imported." in done
    assert '1</span> <span class="text-xs text-red-600 block">row skipped' in done
    assert not Post.objects.filter(workspace=workspace, caption="B34 boom").exists()


# --------------------------------------------------------------------------- editing approved content (AK2)

RETURNS_TO_REVIEW = {"none": False, "optional": False, "required_internal": True, "required_internal_and_client": True}


def _approved(workspace, account, author):
    post = Post.objects.create(workspace=workspace, author=author, title="B34 approved", caption="Approved caption")
    copy = PlatformPost.objects.create(post=post, social_account=account, status="approved")
    ApprovalAction.objects.create(post=post, user=author, action=ApprovalAction.ActionType.APPROVED)
    return post, copy


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("path", ["save_draft", "autosave"])
def test_editing_approved_content_returns_to_review_only_where_required(setup, mode, path):
    workspace, account, actor, client = setup(mode)
    post, copy = _approved(workspace, account, actor)
    data = {"title": post.title, "caption": "Edited caption", "selected_accounts": str(account.pk)}
    if path == "save_draft":
        response = client.post(f"/workspace/{workspace.pk}/compose/{post.pk}/save/", {**data, "action": "save_draft"})
    else:
        response = client.post(f"/workspace/{workspace.pk}/compose/{post.pk}/autosave/", data)
    assert response.status_code in (200, 302)
    post.refresh_from_db()
    copy.refresh_from_db()
    assert post.caption == "Edited caption"
    assert copy.status == ("pending_review" if RETURNS_TO_REVIEW[mode] else "approved")


@pytest.mark.parametrize("mode", MODES)
def test_unchanged_save_keeps_approval_in_every_mode(setup, mode):
    workspace, account, actor, client = setup(mode)
    post, copy = _approved(workspace, account, actor)
    client.post(
        f"/workspace/{workspace.pk}/compose/{post.pk}/save/",
        {"action": "save_draft", "title": post.title, "caption": post.caption, "selected_accounts": str(account.pk)},
    )
    copy.refresh_from_db()
    assert copy.status == "approved"


@pytest.mark.parametrize("mode", MODES)
def test_media_change_and_the_banner_follow_the_same_rule(setup, mode):
    """Media add/upload/remove revert through the same helper; the composer banner says what saving does."""
    workspace, account, actor, client = setup(mode)
    post, copy = _approved(workspace, account, actor)
    page = client.get(f"/workspace/{workspace.pk}/compose/{post.pk}/").content.decode()
    if RETURNS_TO_REVIEW[mode]:
        assert "Approved - saving changes sends it back for re-approval" in page
    else:
        assert re.search(r">Approved</span>", page) and "sends it back for re-approval" not in page
    composer_views._revert_approved_to_review(post)
    copy.refresh_from_db()
    assert copy.status == ("pending_review" if RETURNS_TO_REVIEW[mode] else "approved")


def test_every_option_a_site_uses_the_shared_helper():
    """Only ``_revert_approved_to_review`` turns an edited approved copy back into review in the composer."""
    import pathlib

    source = (pathlib.Path(composer_views.__file__)).read_text(encoding="utf-8")
    body = source.split("def _revert_approved_to_review(post):", 1)[1].split("\ndef ", 1)[0]
    assert "edit_returns_to_review(post.workspace)" in body
    assert source.count("_revert_approved_to_review(post)") == 6  # definition + save, autosave, media ×3


# ---------------------------------------------------------------------------------- calendar drag (AK3)

PLANNERS = {"owner", "manager"}
DRAG_STATUSES = ["draft", "failed", "scheduled", "approved", "pending_review", "pending_client"]


def _expected_drop(mode, role, status):
    if role not in PLANNERS:
        return 403
    if status == "draft" and mode.startswith("required"):
        return 400  # needs approval first
    return 204


@pytest.mark.parametrize("role", ROLES)
@pytest.mark.parametrize("mode", ["none", "required_internal"])
def test_calendar_drop_and_draggable_chip_follow_one_rule(setup, mode, role):
    workspace, account, actor, client = setup(mode, role)
    day = timezone.localdate() + timedelta(days=3)
    when = timezone.make_aware(datetime.combine(day, datetime.min.time().replace(hour=10)))
    copies = {}
    for status in DRAG_STATUSES:
        post = Post.objects.create(
            workspace=workspace, author=actor, title=f"B34 drag {status}", caption="Ready caption"
        )
        copies[status] = PlatformPost.objects.create(
            post=post, social_account=account, status=status, scheduled_at=when
        )

    page = client.get(f"/workspace/{workspace.pk}/calendar/?mode=calendar&view=week&date={day.isoformat()}")
    html = page.content.decode() if page.status_code == 200 else ""
    for status, copy in copies.items():
        expected = _expected_drop(mode, role, status)
        if html:
            assert (f'data-platform-post-id="{copy.pk}"' in html) == (expected == 204), (status, "chip")
        target = (day + timedelta(days=1)).isoformat() + "T15:00:00"
        response = client.post(
            f"/workspace/{workspace.pk}/calendar/reschedule/",
            {"platform_post_id": str(copy.pk), "new_datetime": target},
        )
        assert response.status_code == expected, (status, response.content)
        copy.refresh_from_db()
        if expected == 204:
            assert copy.scheduled_at.date() == day + timedelta(days=1)
            assert copy.status == ("scheduled" if status in {"draft", "failed"} else status)
        else:
            assert copy.scheduled_at == when and copy.status == status
    if role in PLANNERS | {"editor", "contributor"}:
        assert html, "the team sees the calendar"


def test_calendar_chip_of_someone_else_is_not_draggable_without_edit_others(setup, organization):
    """A planner custom role without ``edit_others_posts``: neither the chip nor the drop (same rule)."""
    from apps.members.models import CustomRole

    workspace, account, actor, client = setup("none", "manager")
    membership = WorkspaceMembership.objects.get(workspace=workspace, user=actor)
    permissions = dict(membership.effective_permissions, edit_others_posts=False)
    membership.custom_role = CustomRole.objects.create(
        organization=organization, name="Planner", permissions=permissions
    )
    membership.save(update_fields=["custom_role"])
    author = User.objects.create_user(email="author-b34@example.invalid", tos_accepted_at=timezone.now())
    day = timezone.localdate() + timedelta(days=3)
    when = timezone.make_aware(datetime.combine(day, datetime.min.time().replace(hour=10)))
    post = Post.objects.create(workspace=workspace, author=author, title="B34 other", caption="Ready caption")
    copy = PlatformPost.objects.create(post=post, social_account=account, status="approved", scheduled_at=when)
    html = client.get(
        f"/workspace/{workspace.pk}/calendar/?mode=calendar&view=week&date={day.isoformat()}"
    ).content.decode()
    assert "B34 other" in html and f'data-platform-post-id="{copy.pk}"' not in html
    response = client.post(
        f"/workspace/{workspace.pk}/calendar/reschedule/",
        {"platform_post_id": str(copy.pk), "new_datetime": f"{day.isoformat()}T15:00:00"},
    )
    assert response.status_code == 403
