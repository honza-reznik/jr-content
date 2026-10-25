"""In-app notifications about content (F7-B03): who is told, what they read and where it links."""

import uuid
from unittest.mock import patch

import pytest
from django.core import mail
from django.test import Client, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.approvals import services as approvals
from apps.approvals.models import ApprovalAction
from apps.approvals.tasks import _remind_reviewers
from apps.jrc_channels.services import create_manual_channel
from apps.jrc_production.notifications import notify_text_version
from apps.jrc_production.services import can_decide, create_content, move_content, request_content_changes
from apps.jrc_texts import services as texts
from apps.jrc_texts.models import TextVersion
from apps.jrc_video.models import VideoAsset, VideoVersion
from apps.jrc_video.uploads import _apply_remote
from apps.members.models import WorkspaceMembership
from apps.notifications.audience import content_path, decides_at_step
from apps.notifications.engine import notify
from apps.notifications.models import Channel, EventType, Notification, NotificationDelivery, NotificationPreference
from apps.workspaces.models import Workspace

pytestmark = pytest.mark.django_db

SINGLE = "required_internal"
TWO_STAGE = "required_internal_and_client"
SECRET_SCRIPT = "SCENE ONE: the secret plot twist nobody may read in a notification."
SECRET_NOTE = "TEAM ONLY: the budget is tight"


def make_user(email, name):
    return User.objects.create_user(email=email, password="testpass123", name=name, tos_accepted_at=timezone.now())


def join(workspace, user, role):
    return WorkspaceMembership.objects.update_or_create(
        workspace=workspace, user=user, defaults={"workspace_role": role, "custom_role": None}
    )[0]


def person(workspace, role, name):
    user = make_user(f"{name.lower()}@example.test", name)
    join(workspace, user, role)
    return user


@pytest.fixture
def owner(workspace, user):
    return user


@pytest.fixture
def manager(workspace):
    return person(workspace, "manager", "Mia")


@pytest.fixture
def editor(workspace):
    return person(workspace, "editor", "Eddie")


@pytest.fixture
def contributor(workspace):
    return person(workspace, "contributor", "Cora")


@pytest.fixture
def ella(workspace):
    return person(workspace, "client", "Ella")


@pytest.fixture
def viewer(workspace):
    return person(workspace, "viewer", "Vic")


@pytest.fixture
def everyone(owner, manager, editor, contributor, ella, viewer):
    return {
        "owner": owner,
        "manager": manager,
        "editor": editor,
        "contributor": contributor,
        "client": ella,
        "viewer": viewer,
    }


@pytest.fixture
def content(workspace, owner, everyone):
    create_manual_channel(workspace, "instagram", "Ella Instagram")
    post = create_content(workspace, "Ella garden reel", owner)
    post.internal_notes = SECRET_NOTE
    post.save(update_fields=["internal_notes", "updated_at"])
    Notification.objects.all().delete()
    return post


def set_mode(workspace, mode):
    Workspace.objects.filter(pk=workspace.pk).update(approval_workflow_mode=mode)
    workspace.refresh_from_db()


def to_review(post, user):
    return move_content(post, "review", user, expected_stage="idea")


def got(user, event_type=None):
    qs = Notification.objects.filter(user=user)
    if event_type:
        qs = qs.filter(event_type=event_type)
    return list(qs)


def receivers(event_type):
    return {n.user.email.split("@")[0] for n in Notification.objects.filter(event_type=event_type)}


def save_script(post, user, text):
    document = post.jrc_texts.filter(kind="script").first()
    if document is None:
        document = texts.create_document(post.pk, user, markdown="")
    base = document.versions.filter(state="final").latest("number")
    draft = texts.edit(base.pk, user)
    return texts.save(draft.pk, user, expected_revision=0, markdown=text)


def no_leak(notification):
    text = f"{notification.title} {notification.body}"
    assert SECRET_SCRIPT not in text and SECRET_NOTE not in text


# ---- AK1: script and caption versions ----------------------------------------------------------


def test_script_version_goes_to_the_team_but_not_to_the_author_or_client_in_production(workspace, owner, content):
    team = {"mia", "eddie", "cora"}
    save_script(content, owner, SECRET_SCRIPT)

    assert receivers(EventType.CONTENT_SCRIPT_VERSION) == team
    for notification in Notification.objects.all():
        no_leak(notification)
        assert notification.title == "New script version"
        assert "version 2" in notification.body and "Ella garden reel" in notification.body
        assert notification.data["action_url"] == content_path(content, "script")
        assert notification.data["workspace_id"] == str(workspace.id)


@pytest.mark.parametrize(
    ("writer", "expected"),
    [
        ("owner", {"mia", "eddie", "cora", "ella", "vic"}),
        ("editor", {"owner", "mia", "cora", "ella", "vic"}),
    ],
)
def test_script_version_in_review_also_reaches_the_client_side(workspace, everyone, content, writer, expected):
    owner = everyone["owner"]
    save_script(content, owner, "first")
    to_review(content, owner)
    Notification.objects.all().delete()

    save_script(content, everyone[writer], SECRET_SCRIPT)

    got_names = {n.user.email.split("@")[0] for n in Notification.objects.all()}
    assert got_names == {name if name != "owner" else "test" for name in expected}
    for notification in Notification.objects.all():
        no_leak(notification)
    assert all(n.user != everyone[writer] for n in Notification.objects.all())


def test_unchanged_save_creates_no_version_and_no_notification(workspace, owner, content):
    save_script(content, owner, "same text")
    Notification.objects.all().delete()

    save_script(content, owner, "same text")

    assert Notification.objects.count() == 0


def test_script_created_with_text_notifies_but_an_empty_one_does_not(workspace, owner, content):
    texts.create_document(content.pk, owner, markdown="")
    assert Notification.objects.count() == 0

    other = create_content(workspace, "Second reel", owner)
    Notification.objects.all().delete()
    texts.create_document(other.pk, owner, markdown="Real words", source="api")
    assert receivers(EventType.CONTENT_SCRIPT_VERSION) == {"mia", "eddie", "cora"}


def test_caption_version_names_the_channel_and_is_sent_once(workspace, owner, content):
    variant = content.platform_posts.get()

    texts.save_caption(content.pk, variant.pk, owner, f"Roses at dawn {SECRET_SCRIPT}")

    notifications = Notification.objects.filter(event_type=EventType.CONTENT_CAPTION_VERSION)
    assert {n.user.email.split("@")[0] for n in notifications} == {"mia", "eddie", "cora"}
    assert notifications.count() == 3  # one per person, not one per internal step
    for notification in notifications:
        no_leak(notification)
        assert "Ella Instagram" in notification.body and "Instagram" in notification.body
        assert notification.data["action_url"] == content_path(content, "caption")
    Notification.objects.all().delete()
    latest = texts.latest_saved_captions([variant.pk])[variant.pk]
    texts.save_caption(content.pk, variant.pk, owner, "Roses at dusk", base_version_id=latest.pk)
    assert Notification.objects.filter(event_type=EventType.CONTENT_CAPTION_VERSION).count() == 3


def test_caption_saved_through_the_detail_page_notifies_once(workspace, owner, content):
    variant = content.platform_posts.get()
    client = Client()
    client.force_login(owner)
    url = reverse("jrc_production:content_detail", kwargs={"workspace_id": workspace.id, "post_id": content.id})

    response = client.post(url, {"action": "save_caption", "platform_post": str(variant.pk), "caption": "Hello"})

    assert response.status_code == 302
    assert Notification.objects.filter(event_type=EventType.CONTENT_CAPTION_VERSION).count() == 3


def test_a_failing_notification_does_not_undo_the_save(workspace, owner, content):
    with patch("apps.jrc_production.notifications.notify", side_effect=RuntimeError("boom")):
        version = save_script(content, owner, "kept")

    assert version.state == "final" and version.text.strip() == "kept"


def test_archived_client_and_inactive_users_are_not_notified(workspace, owner, manager, editor, content):
    User.objects.filter(pk=editor.pk).update(is_active=False)

    save_script(content, owner, "words")

    assert receivers(EventType.CONTENT_SCRIPT_VERSION) == {"mia", "cora"}
    Notification.objects.all().delete()
    Workspace.objects.filter(pk=workspace.pk).update(is_archived=True)
    notify_text_version(TextVersion.objects.filter(document__post=content).latest("created_at"), owner)
    assert Notification.objects.count() == 0


# ---- AK2: video versions -----------------------------------------------------------------------


def make_video(content, uploader, status="processing"):
    variant = content.platform_posts.get()
    video = VideoAsset.objects.create(platform_post=variant, ff_asset_id=uuid.uuid4())
    return VideoVersion.objects.create(
        video=video, number=1, ff_version_id=uuid.uuid4(), status=status, uploaded_by=uploader
    )


def test_video_ready_goes_to_the_team_once_and_not_to_the_uploader(workspace, owner, content):
    version = make_video(content, owner)

    _apply_remote(version, {"processing_status": "processing"})
    assert Notification.objects.count() == 0

    _apply_remote(version, {"processing_status": "ready", "files": [{"duration_seconds": 12.5}]})
    _apply_remote(version, {"processing_status": "ready", "files": [{"duration_seconds": 12.5}]})

    assert receivers(EventType.CONTENT_VIDEO_VERSION) == {"mia", "eddie", "cora"}
    assert Notification.objects.count() == 3
    notification = Notification.objects.first()
    assert notification.data["action_url"] == content_path(content, "video")
    assert "Ella garden reel" in notification.body and "Owner" not in notification.title
    version.refresh_from_db()
    assert version.status == "ready" and version.duration_s == 12.5


def test_video_ready_in_review_also_reaches_client_and_viewer(workspace, owner, content, ella, viewer):
    to_review(content, owner)
    version = make_video(content, owner)

    _apply_remote(version, {"processing_status": "ready"})

    assert receivers(EventType.CONTENT_VIDEO_VERSION) == {"mia", "eddie", "cora", "ella", "vic"}


def test_failed_conversion_sends_no_video_notification(workspace, owner, content):
    version = make_video(content, owner)

    _apply_remote(version, {"processing_status": "failed"})

    assert Notification.objects.count() == 0


# ---- AK3: approval request goes to whoever is up next ------------------------------------------


def test_two_stage_submit_asks_the_team_first_and_the_client_afterwards(workspace, everyone, content):
    set_mode(workspace, TWO_STAGE)
    owner, manager = everyone["owner"], everyone["manager"]

    to_review(content, owner)

    assert receivers(EventType.POST_SUBMITTED) == {"mia"}  # owner is the actor; editor/contributor cannot approve
    assert got(everyone["client"]) == [] and got(everyone["viewer"]) == []
    assert got(manager, EventType.POST_SUBMITTED)[0].data["action_url"] == content_path(content, "approval")
    Notification.objects.all().delete()

    move_content(content, "approved", manager, expected_stage="review")

    assert got(everyone["client"], EventType.CLIENT_APPROVAL_REQUESTED)
    assert {n.user.email.split("@")[0] for n in Notification.objects.all()} == {"ella"}
    ask = got(everyone["client"])[0]
    assert ask.data["action_url"] == content_path(content, "approval")
    assert ask.data["workspace_id"] == str(workspace.id)
    assert ask.event_type == EventType.CLIENT_APPROVAL_REQUESTED
    assert Channel.IN_APP == "in_app"  # shown in the app, not only mailed
    assert NotificationDelivery.objects.filter(notification=ask, channel=Channel.IN_APP).exists()


def test_single_stage_submit_asks_everyone_who_can_decide_including_the_client(workspace, everyone, content):
    set_mode(workspace, SINGLE)

    to_review(content, everyone["owner"])

    assert receivers(EventType.POST_SUBMITTED) == {"mia"}
    assert receivers(EventType.CLIENT_APPROVAL_REQUESTED) == {"ella"}
    assert got(everyone["viewer"]) == [] and got(everyone["editor"]) == [] and got(everyone["contributor"]) == []


def test_resubmit_follows_the_same_rule(workspace, everyone, content):
    set_mode(workspace, TWO_STAGE)
    to_review(content, everyone["owner"])
    request_content_changes(content, everyone["manager"], "Shorter please", expected_stage="review")
    Notification.objects.all().delete()

    approvals.resubmit_post(content, everyone["owner"], workspace)

    assert receivers(EventType.POST_SUBMITTED) == {"mia"}
    assert got(everyone["client"]) == []


@pytest.mark.parametrize("mode", [SINGLE, TWO_STAGE])
@pytest.mark.parametrize("role", ["owner", "manager", "editor", "contributor", "client", "viewer"])
@pytest.mark.parametrize("step", ["pending_review", "pending_client"])
def test_notification_rule_matches_can_decide(workspace, content, mode, role, step):
    set_mode(workspace, mode)
    member = person(workspace, role, f"Rule{role}")
    content.platform_posts.update(status=step)
    content.refresh_from_db()

    assert decides_at_step(workspace, member.workspace_memberships.get(workspace=workspace), step) == can_decide(
        content, member.workspace_memberships.get(workspace=workspace)
    )


def test_reminder_for_the_team_step_skips_the_client_in_two_stage_mode(workspace, everyone, content):
    set_mode(workspace, TWO_STAGE)
    to_review(content, everyone["owner"])
    Notification.objects.all().delete()

    _remind_reviewers(content)

    assert receivers(EventType.APPROVAL_REMINDER) == {"test", "mia"}
    assert got(everyone["client"]) == []


# ---- AK4: the client's decision goes to the team ------------------------------------------------


def test_client_approval_tells_the_team_with_a_link(workspace, everyone, content):
    set_mode(workspace, SINGLE)
    to_review(content, everyone["owner"])
    Notification.objects.all().delete()

    move_content(content, "approved", everyone["client"], expected_stage="review")

    approved = Notification.objects.filter(event_type=EventType.POST_APPROVED)
    assert {n.user.email.split("@")[0] for n in approved} == {"test", "mia", "eddie", "cora"}
    assert not any(n.user in (everyone["client"], everyone["viewer"]) for n in Notification.objects.all())
    for notification in approved:
        assert notification.data["action_url"] == content_path(content, "approval")
        assert "Ella" in notification.body or "Ella" in notification.title or "approved" in notification.body
    assert {n.title for n in approved} == {"Post approved", "Client approved a post"}


def test_client_request_for_changes_quotes_the_comment_to_the_team_only(workspace, everyone, content):
    set_mode(workspace, SINGLE)
    to_review(content, everyone["owner"])
    Notification.objects.all().delete()

    request_content_changes(content, everyone["client"], "Please cut the ending at 0:17.", expected_stage="review")

    requested = Notification.objects.filter(event_type=EventType.POST_CHANGES_REQUESTED)
    assert {n.user.email.split("@")[0] for n in requested} == {"test", "mia", "eddie", "cora"}
    for notification in requested:
        assert "Please cut the ending at 0:17." in notification.body
        assert notification.data["note_visibility"] == "external"
        assert notification.data["action_url"] == content_path(content, "approval")
    assert got(everyone["client"]) == []


def test_team_request_for_changes_stays_with_the_author_and_hides_the_internal_note(workspace, everyone, content):
    set_mode(workspace, TWO_STAGE)
    ella = everyone["client"]
    # content created by the client: the client is the author
    mine = create_content(workspace, "Ella's own idea", ella)
    approvals.submit_for_review(mine, ella, workspace)
    Notification.objects.all().delete()

    approvals.request_changes(mine, everyone["manager"], workspace, SECRET_NOTE)

    assert receivers(EventType.POST_CHANGES_REQUESTED) == {"ella"}
    notification = got(ella, EventType.POST_CHANGES_REQUESTED)[0]
    assert notification.data["note_visibility"] == "internal"
    # the stored text still holds the note; what a client reads is replaced on every read
    from apps.notifications.privacy import protect_note_body

    assert SECRET_NOTE not in protect_note_body(notification).body
    client = Client()
    client.force_login(ella)
    page = client.get(reverse("notifications:list")).content.decode()
    drawer = client.get(reverse("notifications:drawer")).content.decode()
    assert SECRET_NOTE not in page and SECRET_NOTE not in drawer
    assert "Changes were requested on your post." in page


def test_hold_requested_by_the_client_reaches_only_the_team(workspace, everyone, content):
    set_mode(workspace, SINGLE)
    other_client = person(workspace, "client", "Greta")
    to_review(content, everyone["owner"])
    move_content(content, "approved", everyone["client"], expected_stage="review")
    Notification.objects.all().delete()

    approvals.request_hold(content, everyone["client"], workspace, "Not now")

    assert receivers(EventType.APPROVAL_HOLD_REQUESTED) == {"test", "mia"}
    assert got(other_client) == []


# ---- AK5: nothing leaves the machine ------------------------------------------------------------


def test_installation_settings_use_console_mail_and_no_webhooks():
    # Read the source: importing the settings module would change the environment of later tests.
    import importlib.util
    from pathlib import Path

    source = Path(importlib.util.find_spec("config.settings.jrc").origin).read_text(encoding="utf-8")

    assert '\nEMAIL_BACKEND_TYPE = "console"\n' in source
    assert "\nNOTIFICATION_WEBHOOKS_ENABLED = False\n" in source


def test_webhook_channel_is_never_used_when_disabled(workspace, owner, manager, content):
    for event in (EventType.CONTENT_SCRIPT_VERSION, EventType.POST_SUBMITTED):
        NotificationPreference.objects.create(user=manager, event_type=event, channel=Channel.WEBHOOK, is_enabled=True)

    with override_settings(NOTIFICATION_WEBHOOKS_ENABLED=False), patch("httpx.post") as post:
        notify(manager, EventType.CONTENT_SCRIPT_VERSION, "t", "b", {"webhook_url": "https://example.com/hook"})
        save_script(content, owner, "words")

    post.assert_not_called()
    assert not NotificationDelivery.objects.filter(channel=Channel.WEBHOOK).exists()


def test_email_of_a_content_notification_is_console_or_local_with_an_absolute_link(workspace, owner, manager, content):
    NotificationPreference.objects.create(
        user=manager, event_type=EventType.CONTENT_SCRIPT_VERSION, channel=Channel.EMAIL, is_enabled=True
    )

    with override_settings(APP_URL="http://127.0.0.1:13000"):
        save_script(content, owner, SECRET_SCRIPT)

    assert len(mail.outbox) == 1
    message = mail.outbox[0]
    assert message.to == [manager.email]
    link = "http://127.0.0.1:13000" + content_path(content, "script")
    assert link in message.body and SECRET_SCRIPT not in message.body
    assert SECRET_SCRIPT not in message.alternatives[0][0]


# ---- AK6: links and the list --------------------------------------------------------------------


def test_notification_list_links_to_the_right_tab_and_the_link_opens(workspace, everyone, content):
    save_script(content, everyone["owner"], "words")
    client = Client()
    client.force_login(everyone["manager"])

    page = client.get(reverse("notifications:list")).content.decode()
    drawer = client.get(reverse("notifications:drawer")).content.decode()

    link = content_path(content, "script")
    assert f'href="{link.replace("&", "&amp;")}"' in page and f'href="{link}"' in drawer
    opened = client.get(link)
    assert opened.status_code == 200 and "Ella garden reel" in opened.content.decode()


def test_other_clients_notifications_cannot_be_read_through_the_list(workspace, everyone, content):
    save_script(content, everyone["owner"], "words")
    client = Client()
    client.force_login(everyone["client"])

    page = client.get(reverse("notifications:list")).content.decode()

    assert "saved version" not in page


def test_decision_text_for_the_author_is_unchanged(workspace, everyone, content):
    set_mode(workspace, SINGLE)
    to_review(content, everyone["owner"])
    Notification.objects.all().delete()
    approvals.approve_post(content, everyone["manager"], workspace)

    notification = got(everyone["owner"], EventType.POST_APPROVED)[0]
    assert notification.title == "Post approved" and "approved by Mia" in notification.body
    assert ApprovalAction.objects.filter(post=content, action="approved").exists()


# ---- Review fixes (Luna 04:18) ----------------------------------------------------------------


def test_pending_webhook_deliveries_are_closed_when_webhooks_are_off(workspace, manager):
    from datetime import timedelta

    from apps.notifications.engine import _dispatch_webhook, retry_failed_deliveries

    notification = Notification.objects.create(
        user=manager, event_type=EventType.POST_SUBMITTED, title="t", data={"webhook_url": "https://example.com/hook"}
    )
    delivery = NotificationDelivery.objects.create(
        notification=notification,
        channel=Channel.WEBHOOK,
        status="pending",
        attempts=1,
        next_retry_at=timezone.now() - timedelta(minutes=1),
    )

    with override_settings(NOTIFICATION_WEBHOOKS_ENABLED=False), patch("httpx.post") as post:
        assert retry_failed_deliveries() == 1
        with pytest.raises(RuntimeError):
            _dispatch_webhook(delivery)

    post.assert_not_called()
    delivery.refresh_from_db()
    assert delivery.status == "failed" and delivery.next_retry_at is None and delivery.attempts == 1


@pytest.mark.parametrize(
    ("url", "kept"),
    [
        ("/workspace/x/content/y/?tab=script", True),
        ("javascript:alert(1)", False),
        ("//evil.example/x", False),
        ("https://evil.example/x", False),
        ("/\\evil.example", False),
        ("/ok path", False),
        ("", False),
    ],
)
def test_only_in_app_paths_become_links(workspace, manager, url, kept):
    Notification.objects.create(
        user=manager, event_type=EventType.POST_SUBMITTED, title="Link test", data={"action_url": url}
    )
    client = Client()
    client.force_login(manager)

    page = client.get(reverse("notifications:list")).content.decode()
    drawer = client.get(reverse("notifications:drawer")).content.decode()

    for html in (page, drawer):
        assert (f'href="{url}"' in html) is kept
        assert "javascript:" not in html and "evil.example" not in html
    from apps.notifications.engine import action_link

    notification = Notification.objects.get(title="Link test")
    assert bool(action_link(notification, "http://127.0.0.1:13000")) is kept


def test_client_step_request_skips_clients_who_cannot_decide_or_are_inactive(workspace, everyone, content):
    from apps.members.models import CustomRole

    set_mode(workspace, TWO_STAGE)
    read_only = CustomRole.objects.create(organization=workspace.organization, name="Read only", permissions={})
    quiet = person(workspace, "client", "Quiet")
    WorkspaceMembership.objects.filter(user=quiet, workspace=workspace).update(custom_role=read_only)
    gone = person(workspace, "client", "Gone")
    User.objects.filter(pk=gone.pk).update(is_active=False)
    to_review(content, everyone["owner"])
    Notification.objects.all().delete()

    move_content(content, "approved", everyone["manager"], expected_stage="review")

    assert receivers(EventType.CLIENT_APPROVAL_REQUESTED) == {"ella"}


@pytest.mark.parametrize("how", ["removed", "inactive"])
def test_decision_does_not_reach_an_author_who_left(workspace, everyone, content, how):
    set_mode(workspace, SINGLE)
    ella = everyone["client"]
    mine = create_content(workspace, "Ella's own idea", ella)
    approvals.submit_for_review(mine, ella, workspace)
    if how == "removed":
        WorkspaceMembership.objects.filter(user=ella, workspace=workspace).delete()
    else:
        User.objects.filter(pk=ella.pk).update(is_active=False)
    Notification.objects.all().delete()

    approvals.approve_post(mine, everyone["manager"], workspace)
    approvals.request_changes(mine, everyone["manager"], workspace, "Rework")

    assert Notification.objects.filter(user=ella).count() == 0
