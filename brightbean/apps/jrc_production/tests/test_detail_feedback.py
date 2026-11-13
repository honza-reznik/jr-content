"""P2-B04: detail edits, mutable version metadata and the Discussion link contract."""

import uuid

import pytest
from django.db import IntegrityError, connection, transaction
from django.urls import reverse

from apps.composer.models import ContentCategory, PlatformPost, Post
from apps.jrc_channels.services import create_manual_channel
from apps.jrc_production.models import ContentCategoryChange, ContentRename
from apps.jrc_texts import services
from apps.jrc_texts.models import TextVersion, TextVersionNameChange
from apps.members.models import WorkspaceMembership
from apps.notifications.audience import content_path
from apps.workspaces.models import Workspace

pytestmark = pytest.mark.django_db


@pytest.fixture
def data(workspace, user, client):
    post = Post.objects.create(workspace=workspace, author=user, title="Old title")
    account = create_manual_channel(workspace, "instagram", "Test channel")
    variant = PlatformPost.objects.create(post=post, social_account=account)
    client.force_login(user)
    return post, variant


def detail(post, tab):
    return reverse("jrc_production:content_detail", args=[post.workspace_id, post.pk]) + f"?tab={tab}"


def name_url(post, version):
    return reverse("jrc_texts:version_name", args=[post.workspace_id, post.pk, version.pk])


@pytest.mark.parametrize("kind", ["concept", "script", "caption"])
def test_name_saved_versions_without_changing_content_or_author(data, user, client, kind):
    post, variant = data
    doc = services.create_document(
        post.pk,
        user,
        kind=kind,
        markdown="Original text",
        platform_post_id=variant.pk if kind == "caption" else None,
        format="plain" if kind == "caption" else "rich",
    )
    version = doc.versions.get()
    original = (version.content, version.created_by_id, version.finalized_at)
    response = client.post(name_url(post, version), {"name": "  Opening hook  "})
    assert response.status_code == 200
    version.refresh_from_db()
    assert version.name == "Opening hook"
    assert (version.content, version.created_by_id, version.finalized_at) == original
    event = TextVersionNameChange.objects.get(version=version)
    assert (event.old_name, event.new_name, event.user) == ("", "Opening hook", user)
    assert "Opening hook" in client.get(detail(post, "history")).content.decode()
    if kind != "caption":
        state = client.get(reverse(f"jrc_texts:{kind}", args=[post.workspace_id, post.pk])).json()
        assert state["versions"][0]["name"] == state["shown"]["name"] == "Opening hook"
    assert client.post(name_url(post, version), {"name": "Opening hook"}).status_code == 200
    assert TextVersionNameChange.objects.count() == 1
    assert client.post(name_url(post, version), {"name": ""}).status_code == 200
    assert TextVersionNameChange.objects.count() == 2
    version.refresh_from_db()
    assert version.name == ""
    assert client.post(name_url(post, version), {"name": "x" * 101}).status_code == 400
    assert TextVersionNameChange.objects.count() == 2
    # Naming must not weaken the deferred PostgreSQL protection of saved text.
    if connection.vendor == "postgresql":
        with pytest.raises(IntegrityError), transaction.atomic():
            TextVersion.objects.filter(pk=version.pk).update(text="Rewritten")
            with connection.cursor() as cursor:
                cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")


@pytest.mark.parametrize(
    "role, own, allowed",
    [
        ("owner", False, True),
        ("manager", False, True),
        ("editor", False, True),
        ("contributor", True, True),
        ("contributor", False, False),
        ("client", True, False),
        ("viewer", True, False),
    ],
)
def test_metadata_writes_follow_content_permissions(data, user, client, role, own, allowed):
    post, _ = data
    doc = services.create_document(post.pk, user, kind="concept", markdown="Text")
    if not own:
        post.author = None
        post.save(update_fields=["author"])
    WorkspaceMembership.objects.filter(workspace=post.workspace, user=user).update(workspace_role=role)
    version = doc.versions.get()
    response = client.post(name_url(post, version), {"name": "Name"})
    assert response.status_code == (200 if allowed else 403)
    category = ContentCategory.objects.create(workspace=post.workspace, name="News")
    response = client.post(
        reverse("jrc_production:category", args=[post.workspace_id, post.pk]), {"category": category.pk}
    )
    assert response.status_code == (200 if allowed else 403)


def test_category_is_scoped_clearable_and_audited(data, client):
    post, _ = data
    url = reverse("jrc_production:category", args=[post.workspace_id, post.pk])
    category = ContentCategory.objects.create(workspace=post.workspace, name="News")
    other = Workspace.objects.create(organization=post.workspace.organization, name="Other")
    foreign = ContentCategory.objects.create(workspace=other, name="Secret")
    for invalid in (foreign.pk, uuid.uuid4(), "invalid"):
        assert client.post(url, {"category": invalid}).status_code == 400
    assert not ContentCategoryChange.objects.exists()
    assert client.post(url, {"category": category.pk}).status_code == 200
    assert client.post(url, {"category": category.pk}).status_code == 200
    assert ContentCategoryChange.objects.count() == 1
    post.refresh_from_db()
    assert post.category == category
    assert "Category changed from “None” to “News”" in client.get(detail(post, "history")).content.decode()
    assert client.post(url, {"category": ""}).status_code == 200
    post.refresh_from_db()
    assert post.category is None
    assert ContentCategoryChange.objects.count() == 2


def test_version_name_rejects_another_post_and_draft(data, user, client):
    post, _ = data
    other = Post.objects.create(workspace=post.workspace, author=user)
    doc = services.create_document(other.pk, user, kind="script", markdown="Secret")
    version = doc.versions.get()
    assert client.post(name_url(post, version), {"name": "Hack"}).status_code == 404
    draft = services.edit(version.pk, user)
    assert client.post(name_url(other, draft), {"name": "Draft"}).status_code == 404
    assert not TextVersionNameChange.objects.exists()


def test_inline_rename_uses_existing_approval_and_history(data, client):
    post, variant = data
    post.workspace.approval_workflow_mode = "required_internal"
    post.workspace.save()
    variant.status = "approved"
    variant.save()
    url = reverse("jrc_production:rename", args=[post.workspace_id, post.pk])
    assert client.post(url, {"title": "  "}, HTTP_ACCEPT="application/json").status_code == 400
    response = client.post(url, {"title": "New title"}, HTTP_ACCEPT="application/json")
    assert response.status_code == 200
    assert response.json()["title"] == "New title"
    variant.refresh_from_db()
    assert variant.status == "pending_review"
    assert ContentRename.objects.get(post=post).old_title == "Old title"


def test_caption_metadata_copy_and_removed_preview(data, user, client):
    post, variant = data
    first, _ = services.save_caption(post.pk, variant.pk, user, "First caption")
    services.save_caption(post.pk, variant.pk, user, "Second caption", base_version_id=first.pk)
    page = client.get(detail(post, "caption")).content.decode()
    assert 'data-copy-target="caption-1"' in page
    assert user.display_name in page
    assert "Saved versions" in page
    assert "Instagram preview" not in page
    assert "data-caption-count" in page
    assert "data-version-name" in page
    versions = list(variant.jrc_texts.get().versions.filter(state="final"))
    assert all(version.finalized_at.strftime("%Y") in page for version in versions)


def test_copy_controls_and_discussion_alias(data, client):
    post, _ = data
    for kind in ("concept", "script"):
        assert 'id="script-copy"' in client.get(detail(post, kind)).content.decode()
    assert "data-copy-shotlist" in client.get(detail(post, "shotlist")).content.decode()
    for tab in ("comments", "discussion"):
        response = client.get(detail(post, tab))
        assert response.status_code == 200
        assert response.context["active_tab"] == "discussion"
        assert ">Discussion</h2>" in response.content.decode()
    assert content_path(post, "comments").endswith("?tab=discussion")
