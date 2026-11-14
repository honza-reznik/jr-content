"""P1-B08: the Concept tab — the idea worked out in versions, with the same rules as the script."""

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, connection, transaction
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.composer.models import PlatformPost, Post
from apps.jrc_channels.services import create_manual_channel
from apps.jrc_production.services import create_content, move_content
from apps.jrc_texts import services
from apps.jrc_texts.models import TextDocument, TextVersion
from apps.members.models import WorkspaceMembership
from apps.notifications.audience import content_path
from apps.notifications.models import EventType, Notification

pytestmark = pytest.mark.django_db


def content(text):
    return {"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": text}]}]}


@pytest.fixture
def post(workspace, user):
    return Post.objects.create(workspace=workspace, author=user, title="Concept workflow")


def url(post, kind="concept"):
    return reverse(f"jrc_texts:{kind}", kwargs={"workspace_id": post.workspace_id, "post_id": post.pk})


def detail(post, tab=None):
    path = reverse("jrc_production:content_detail", args=[post.workspace_id, post.pk])
    return f"{path}?tab={tab}" if tab else path


def to_script_url(post):
    return reverse("jrc_texts:concept_to_script", args=[post.workspace_id, post.pk])


@pytest.fixture
def http(client, user):
    client.force_login(user)
    return client


def member(workspace, role, name):
    person = User.objects.create_user(
        email=f"{name}@example.test", password="testpass123", name=name.title(), tos_accepted_at=timezone.now()
    )
    WorkspaceMembership.objects.create(workspace=workspace, user=person, workspace_role=role)
    return person


def login(person):
    client = Client()
    client.force_login(person)
    return client


def send(http, post, action, kind="concept", **data):
    return http.post(url(post, kind), {"action": action, **data}, content_type="application/json")


_devices = iter(range(10_000))


def save_concept(http, post, text, kind="concept"):
    """P2-B08: an automatic save from a fresh device, so every call is a new saved version."""
    state = http.get(url(post, kind)).json()
    revision = next((v["revision"] for v in state["versions"] if v["id"] == state["latest"]), 0)
    return send(
        http,
        post,
        "autosave",
        kind=kind,
        base=state["latest"],
        revision=revision,
        device=f"concept-device-{next(_devices):05d}",
        content=content(text),
    ).json()


# ---- AK1: versions like the script ------------------------------------------------------------


def test_concept_versions_are_separate_from_the_script(http, post):
    assert http.get(url(post)).json()["versions"] == []
    saved = save_concept(http, post, "Hook: three plants")
    assert saved["shown"]["number"] == 1
    assert saved["shown"]["content"] == content("Hook: three plants")
    # The script stays untouched and empty.
    assert http.get(url(post, "script")).json()["versions"] == []
    assert TextDocument.objects.get(post=post, kind="concept").format == "rich"
    assert not TextDocument.objects.filter(post=post, kind="script").exists()

    # Compare and older versions work as for the script.
    second = save_concept(http, post, "Hook: four plants")
    assert second["comparison"]["old"]["number"] == 1 and second["comparison"]["new"]["number"] == 2
    old = http.get(url(post), {"version": saved["latest"]}).json()
    assert old["shown"]["content"] == content("Hook: three plants")


def test_saved_concept_versions_are_immutable(http, post):
    save_concept(http, post, "Kept forever")
    version = TextVersion.objects.filter(document__kind="concept", state="final").latest("number")
    for statement in (
        "UPDATE jrc_texts_textversion SET text = 'overwritten' WHERE id = %s",
        "DELETE FROM jrc_texts_textversion WHERE id = %s",
    ):
        with (
            pytest.raises(IntegrityError, match="Saved text versions"),
            transaction.atomic(),
            connection.cursor() as cursor,
        ):
            cursor.execute(statement, [version.pk])
            cursor.execute("SET CONSTRAINTS jrc_texts_protect_final IMMEDIATE")
    version.refresh_from_db()
    assert version.text == "Kept forever"


def test_concept_conflicts_return_409(http, post):
    first = save_concept(http, post, "First")
    # A concept version can never be addressed through the script endpoint (or vice versa).
    data = {"base": first["latest"], "revision": 0, "device": "concept-device-x1", "content": content("x")}
    assert send(http, post, "autosave", kind="script", **data).status_code == 409
    assert send(http, post, "autosave", **{**data, "base": str(post.pk)}).status_code == 409


def test_one_concept_per_post_and_never_on_a_channel_copy(workspace, user, post):
    services.create_document(post.pk, user, kind="concept", markdown="One")
    with pytest.raises(IntegrityError), transaction.atomic():
        TextDocument.objects.create(post=post, workspace=workspace, kind="concept")
    channel = create_manual_channel(workspace, "instagram", "IG")
    variant = PlatformPost.objects.create(post=post, social_account=channel)
    with pytest.raises(IntegrityError), transaction.atomic():
        TextDocument.objects.create(
            post=Post.objects.create(workspace=workspace, author=user, title="Other"),
            workspace=workspace,
            kind="concept",
            platform_post=variant,
        )


# ---- AK2: tab order and default tab -----------------------------------------------------------


def test_tab_order_and_default_tab_until_a_script_exists(http, post):
    page = http.get(detail(post)).content.decode()
    tabs = [
        name
        for name in (
            "concept",
            "script",
            "shotlist",
            "video",
            "caption",
            "approval",
            "discussion",
            "publish",
            "history",
        )
    ]
    positions = [page.index(f'href="?tab={name}"') for name in tabs]
    assert positions == sorted(positions)
    # A new content (also a quick idea) opens on the Concept.
    assert 'aria-selected="true" href="?tab=concept"' in page
    assert url(post) in page and "jrc_texts/dist/script.js" in page
    assert 'data-text-color="#0369a1"' in page
    assert "Use as script draft" in page
    # An empty script is not a script yet.
    services.create_document(post.pk, post.author, kind="script", markdown="")
    assert 'aria-selected="true" href="?tab=concept"' in http.get(detail(post)).content.decode()
    save_concept(http, post, "Scene one", kind="script")
    page = http.get(detail(post)).content.decode()
    assert 'aria-selected="true" href="?tab=script"' in page
    assert url(post, "script") in page and "Use as script draft" not in page
    # An explicit tab always wins; an unknown one falls back to the default.
    assert 'aria-selected="true" href="?tab=concept"' in http.get(detail(post, "concept")).content.decode()
    assert 'aria-selected="true" href="?tab=script"' in http.get(detail(post, "nonsense")).content.decode()


# ---- AK3: Use as script draft -----------------------------------------------------------------


def test_use_as_script_draft_copies_the_latest_concept(http, post):
    response = http.post(to_script_url(post))
    assert response.status_code == 302 and response["Location"].endswith("?tab=concept")
    assert not TextDocument.objects.filter(post=post, kind="script").exists()

    save_concept(http, post, "Idea one")
    save_concept(http, post, "Idea two")
    response = http.post(to_script_url(post))
    assert response.status_code == 302 and response["Location"].endswith("?tab=script")
    script = http.get(url(post, "script")).json()
    assert len(script["versions"]) == 1
    assert script["shown"]["content"] == content("Idea two")

    # The same text again creates no version; a changed concept adds one on top of the script.
    http.post(to_script_url(post))
    assert len(http.get(url(post, "script")).json()["versions"]) == 1
    save_concept(http, post, "Idea three")
    http.post(to_script_url(post))
    script = http.get(url(post, "script")).json()
    assert [v["number"] for v in script["versions"]] == [2, 1]
    assert script["shown"]["content"] == content("Idea three")
    assert http.get(url(post), {}).json()["shown"]["content"] == content("Idea three")


def test_use_as_script_draft_never_overwrites_a_draft_another_writer_holds(http, post, workspace, user):
    """REST/MCP write through a short-lived draft; one that exists blocks the copy (nothing is overwritten)."""
    save_concept(http, post, "Concept text")
    document = services.create_document(post.pk, user, kind="script", markdown="", initial_draft=True)
    response = http.post(to_script_url(post), follow=True)
    assert b"Someone is editing the script" in response.content
    assert TextVersion.objects.filter(document=document, state="final").count() == 0


def test_use_as_script_draft_requires_a_text_editor(post, workspace, user):
    services.create_document(post.pk, user, kind="concept", markdown="Concept")
    client = login(member(workspace, "client", "ella"))
    assert client.post(to_script_url(post)).status_code == 403
    assert client.get(to_script_url(post)).status_code == 405
    with pytest.raises(PermissionDenied):
        services.copy_concept_to_script(post.pk, User.objects.get(email="ella@example.test"))
    assert not TextDocument.objects.filter(post=post, kind="script").exists()
    with pytest.raises(ValidationError):
        services.copy_concept_to_script(Post.objects.create(workspace=workspace, author=user, title="Empty").pk, user)


# ---- AK4: permissions and notifications -------------------------------------------------------


@pytest.mark.parametrize(
    ("role", "own_post", "can_write"),
    [
        ("owner", False, True),
        ("manager", False, True),
        ("editor", False, True),
        ("contributor", True, True),
        ("contributor", False, False),
        ("client", True, False),
        ("viewer", False, False),
    ],
)
def test_concept_permissions_match_the_script(post, workspace, user, role, own_post, can_write):
    save_concept(login(user), post, "Saved concept")
    person = member(workspace, role, f"p-{role}")
    if own_post:
        Post.objects.filter(pk=post.pk).update(author=person)
    client = login(person)
    # Every member reads saved versions.
    state = client.get(url(post)).json()
    assert state["can_edit"] is can_write
    assert state["shown"]["content"] == content("Saved concept")
    response = send(
        client,
        post,
        "autosave",
        base=state["latest"],
        revision=0,
        device="concept-device-perm",
        content=content("Mine"),
    )
    assert response.status_code == (200 if can_write else 403)
    script = client.get(url(post, "script")).json()
    assert script["can_edit"] is state["can_edit"]


def test_concept_version_notifies_like_the_script(workspace, user):
    create_manual_channel(workspace, "instagram", "IG")
    post = create_content(
        workspace,
        "Concept reel",
        user,
        social_account_ids=workspace.social_accounts.filter(jrc_channel__is_enabled=True).values_list("pk", flat=True),
    )
    manager = member(workspace, "manager", "mia")
    ella = member(workspace, "client", "ella")
    Notification.objects.all().delete()
    save_concept(login(user), post, "Idea for the team")
    notifications = list(Notification.objects.filter(event_type=EventType.CONTENT_SCRIPT_VERSION))
    assert [n.user for n in notifications] == [manager]
    assert notifications[0].title == "New concept version"
    assert "of the concept for" in notifications[0].body and "Idea for the team" not in notifications[0].body
    assert notifications[0].data["action_url"] == content_path(post, "concept")
    # The client side is told only once the content is in Review, the same as for the script.
    assert not Notification.objects.filter(user=ella).exists()
    # P1-B32: Review exists only where the approval mode has one.
    workspace.approval_workflow_mode = "optional"
    workspace.save(update_fields=["approval_workflow_mode"])
    post.workspace.refresh_from_db()
    move_content(post, "review", user, expected_stage="idea")
    Notification.objects.all().delete()
    save_concept(login(user), post, "Second idea")
    assert Notification.objects.filter(user=ella, data__action_url=content_path(post, "concept")).exists()
