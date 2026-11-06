"""P1-B30: every page and e-mail shows user text exactly once escaped (no ``&amp;lt;``, no raw markup).

E-13b-ex10-01: the Approval card showed the script's Markdown (``&lt;`` stored for ``<``) as text, so the
page escaped it a second time. The same check runs over every preview of user text.
"""

from pathlib import Path

import pytest
from django.conf import settings
from django.core import mail
from django.template.loader import render_to_string
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from django.utils.html import escape

from apps.accounts.models import User
from apps.approvals.comments import create_comment
from apps.common.testing import run_email_queue
from apps.jrc_channels.services import create_manual_channel
from apps.jrc_production import shotlist
from apps.jrc_production.services import create_content, move_content
from apps.jrc_texts import services as texts
from apps.jrc_texts.models import TextVersion
from apps.members.models import WorkspaceMembership
from apps.notifications.models import Channel, EventType, NotificationPreference

pytestmark = pytest.mark.django_db

PAYLOAD = "<script>alert(1)</script> & \"q\" 's' a < b > c"
ONCE = escape(PAYLOAD)  # &lt;script&gt;alert(1)&lt;/script&gt; &amp; &quot;q&quot; &#x27;s&#x27; a &lt; b &gt; c
DOUBLE_MARKERS = ("&amp;lt;", "&amp;gt;", "&amp;amp;", "&amp;quot;", "&amp;#x27;", "&amp;#39;")
RAW_MARKUP = "<script>alert(1)</script>"


def doc(*paragraphs):
    return {
        "type": "doc",
        "content": [{"type": "paragraph", "content": [{"type": "text", "text": text}]} for text in paragraphs],
    }


def assert_single_escape(html):
    assert RAW_MARKUP not in html
    for marker in DOUBLE_MARKERS:
        assert marker not in html, marker


@pytest.fixture
def client_user(workspace):
    person = User.objects.create_user(
        email="client@example.test", password="testpass123", name="Cleo Client", tos_accepted_at=timezone.now()
    )
    WorkspaceMembership.objects.create(workspace=workspace, user=person, workspace_role="client")
    return person


@pytest.fixture
def content(workspace, user, client_user):
    create_manual_channel(workspace, "instagram", "Insta " + PAYLOAD)
    post = create_content(
        workspace,
        "Title " + PAYLOAD,
        user,
        social_account_ids=workspace.social_accounts.filter(jrc_channel__is_enabled=True).values_list("pk", flat=True),
    )
    texts.create_document(post.pk, user, kind="concept", content=doc("Concept " + PAYLOAD))
    texts.create_document(post.pk, user, content=doc("Script " + PAYLOAD, "Second " + PAYLOAD))
    variant = post.platform_posts.get()
    texts.save_caption(post.pk, variant.pk, user, "Caption " + PAYLOAD)
    shotlist.add_item(post, user, "Shot " + PAYLOAD, note="Note " + PAYLOAD)
    create_comment(post, user, "Comment " + PAYLOAD, visibility="external")
    move_content(post, "review", user, expected_stage="idea")
    return post


def detail(post, tab):
    url = reverse("jrc_production:content_detail", kwargs={"workspace_id": post.workspace_id, "post_id": post.id})
    return f"{url}?tab={tab}"


def test_script_is_stored_as_markdown_with_entities(content):
    """The precondition of the bug: the stored Markdown is not the text people wrote."""
    version = TextVersion.objects.get(document__post=content, document__kind="script")
    assert "&lt;script&gt;" in version.text
    assert version.content["content"][0]["content"][0]["text"] == "Script " + PAYLOAD


@pytest.mark.parametrize("role", ["owner", "client"])
def test_approval_card_escapes_script_and_caption_once(content, user, client_user, role):
    browser = Client()
    browser.force_login(user if role == "owner" else client_user)

    html = browser.get(detail(content, "approval")).content.decode()

    assert_single_escape(html)
    assert f"Script {ONCE}\n\nSecond {ONCE}</div>" in html
    assert f"Caption {ONCE}" in html


@pytest.mark.parametrize(
    "tab, expected",
    [
        ("approval", ["Script ", "Caption "]),
        ("caption", ["Caption "]),
        ("shotlist", ["Shot ", "Note "]),
        ("comments", ["Comment "]),
        ("history", []),
        ("publish", ["Caption "]),
        ("concept", []),
        ("script", []),
    ],
)
def test_every_content_tab_escapes_user_text_once(content, user, tab, expected):
    browser = Client()
    browser.force_login(user)

    response = browser.get(detail(content, tab))

    assert response.status_code == 200
    html = response.content.decode()
    assert_single_escape(html)
    assert f"Title {ONCE}" in html
    for prefix in expected:
        assert prefix + ONCE in html


@pytest.mark.parametrize("kind", ["script", "concept"])
def test_editor_tabs_receive_the_original_text_not_markdown(content, user, kind):
    """Script and Concept are drawn by the editor from its JSON; the text inside is the written one."""
    browser = Client()
    browser.force_login(user)
    url = reverse(f"jrc_texts:{kind}", kwargs={"workspace_id": content.workspace_id, "post_id": content.id})

    shown = browser.get(url).json()["shown"]["content"]

    assert shown["content"][0]["content"][0]["text"].endswith(PAYLOAD)


@pytest.mark.parametrize(
    "name, query",
    [
        ("jrc_production:board", ""),
        ("calendar:calendar", "?mode=list&tab=queue"),
        ("calendar:calendar", "?mode=calendar&view=list"),
        ("notifications:list", ""),
    ],
)
def test_lists_escape_user_text_once(content, user, client_user, name, query):
    browser = Client()
    browser.force_login(client_user if name == "notifications:list" else user)
    kwargs = {"workspace_id": content.workspace_id} if name.startswith("calendar") else {}

    response = browser.get(reverse(name, kwargs=kwargs) + query)

    assert response.status_code == 200
    assert_single_escape(response.content.decode())


def test_notification_email_has_plain_text_once_and_html_escaped_once(content, user, client_user):
    NotificationPreference.objects.create(
        user=client_user, event_type=EventType.CONTENT_SCRIPT_VERSION, channel=Channel.EMAIL, is_enabled=True
    )
    draft = texts.edit(TextVersion.objects.get(document__post=content, document__kind="script").pk, user)
    mail.outbox.clear()

    texts.save(draft.pk, user, expected_revision=0, content=doc("Changed " + PAYLOAD))
    run_email_queue()

    message = mail.outbox[-1]
    assert f'for "Title {PAYLOAD}"' in message.body
    for entity in ("&lt;", "&gt;", "&amp;", "&quot;", "&#x27;"):
        assert entity not in message.body
    html = message.alternatives[0][0]
    assert f"Title {ONCE}" in html
    assert_single_escape(html)


TEXT_EMAIL_CONTEXT = {
    "notification": {"title": "Title " + PAYLOAD, "body": "Body " + PAYLOAD},
    "heading": "Heading " + PAYLOAD,
    "notifications": [{"title": "Item " + PAYLOAD, "body": "Body " + PAYLOAD, "created_at": timezone.now()}],
    "total": 1,
    "client_user": {"display_name": "Name " + PAYLOAD},
    "created_by": {"display_name": "Author " + PAYLOAD},
    "invited_by": {"display_name": "Author " + PAYLOAD},
    "workspace": {"name": "Workspace " + PAYLOAD},
    "workspace_name": "Workspace " + PAYLOAD,
    "org_name": "Org " + PAYLOAD,
    "action_link": "https://example.test/x?tab=a&b=1",
    "magic_url": "https://example.test/x?tab=a&b=1",
    "accept_url": "https://example.test/x?tab=a&b=1",
    "link_url": "https://example.test/x?tab=a&b=1",
    "app_url": "https://example.test",
}


def text_email_templates():
    root = Path(settings.BASE_DIR) / "templates"
    return sorted(
        str(path.relative_to(root))
        for path in root.rglob("*.txt")
        if "email" in path.parts and "{{" in path.read_text(encoding="utf-8")
    )


def test_every_plain_text_email_template_turns_html_escaping_off():
    """Plain-text e-mail is not HTML: escaping would show entities and break links that contain "&"."""
    templates = text_email_templates()
    assert "notifications/email/notification.txt" in templates
    root = Path(settings.BASE_DIR) / "templates"
    for name in templates:
        assert "{% autoescape off %}" in (root / name).read_text(encoding="utf-8"), name


@pytest.mark.parametrize(
    "name, has_link",
    [
        ("notifications/email/notification.txt", True),
        ("notifications/email/digest.txt", False),
        ("client_portal/email/magic_link.txt", True),
        ("members/email/invite.txt", True),
        ("onboarding/email/connection_link.txt", True),
    ],
)
def test_plain_text_emails_render_text_and_links_unchanged(name, has_link):
    text = render_to_string(name, TEXT_EMAIL_CONTEXT)

    assert PAYLOAD in text
    for entity in ("&lt;", "&gt;", "&amp;", "&quot;", "&#x27;"):
        assert entity not in text
    assert ("https://example.test/x?tab=a&b=1" in text) is has_link
