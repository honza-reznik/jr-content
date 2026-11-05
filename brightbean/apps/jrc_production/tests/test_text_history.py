"""P1-B14: saved versions share the existing audit timeline without exposing drafts."""

import pytest
from django.urls import reverse

from apps.composer.models import PlatformPost, Post
from apps.jrc_channels.services import create_manual_channel
from apps.jrc_production.models import StageChange
from apps.jrc_texts import services
from apps.jrc_texts.models import TextDocument
from apps.members.models import WorkspaceMembership

pytestmark = pytest.mark.django_db


@pytest.fixture
def post(workspace, user):
    return Post.objects.create(workspace=workspace, author=user, title="Text history")


@pytest.fixture
def variant(workspace, post):
    account = create_manual_channel(workspace, "instagram", "History channel")
    return PlatformPost.objects.create(post=post, social_account=account)


@pytest.mark.parametrize("role", ["owner", "client"])
def test_history_includes_saved_texts_channel_author_time_and_stage(client, workspace, user, post, variant, role):
    concept = services.create_document(post.pk, user, kind="concept", markdown="Concept")
    script = services.create_document(post.pk, user, markdown="Script")
    caption, _ = services.save_caption(post.pk, variant.pk, user, "Caption")
    draft = services.edit(concept.versions.get().pk, user)
    services.update_draft(draft.pk, user, expected_revision=0, markdown="PRIVATE DRAFT")
    StageChange.objects.create(post=post, from_stage="idea", to_stage="concept", user=user)
    other = Post.objects.create(workspace=workspace, author=user, title="Other content")
    services.create_document(other.pk, user, markdown="Other private text")
    WorkspaceMembership.objects.filter(workspace=workspace, user=user).update(workspace_role=role)
    client.force_login(user)
    response = client.get(reverse("jrc_production:content_detail", args=[workspace.pk, post.pk]), {"tab": "history"})
    history = response.context["history"]
    texts = [item for item in history if item["kind"] == "text"]
    assert len(texts) == 3
    assert all(item["actor"] == user and item["created_at"] for item in texts)
    assert any(item["created_at"] == script.versions.get().finalized_at for item in texts)
    assert any(item["created_at"] == caption.finalized_at for item in texts)
    page = response.content.decode()
    for label in [
        "Saved Concept version 1",
        "Saved Script version 1",
        "Saved Caption version 1",
        variant.social_account.account_name,
        variant.social_account.get_platform_display(),
        user.display_name,
    ]:
        assert label in page
    assert any(item["kind"] == "stage" for item in history)
    assert [item["created_at"] for item in history] == sorted([item["created_at"] for item in history], reverse=True)
    assert "PRIVATE DRAFT" not in page and "Other private text" not in page


@pytest.mark.parametrize("kind", ["concept", "script"])
def test_legacy_empty_first_version_is_kept_and_explained(client, post, user, kind):
    document = services.create_document(post.pk, user, kind=kind, markdown="")
    first = document.versions.get()
    draft = services.edit(first.pk, user)
    saved = services.save(draft.pk, user, expected_revision=0, markdown="Real text")
    assert saved.number == 2
    first.refresh_from_db()
    assert first.number == 1 and first.text == ""
    client.force_login(user)
    response = client.get(reverse("jrc_production:content_detail", args=[post.workspace_id, post.pk]), {"tab": kind})
    assert b"Version 1 is an empty initial version" in response.content


def test_empty_caption_starts_at_one_and_existing_caption_is_preserved(post, variant, user, workspace):
    variant.post.caption = ""
    variant.post.save(update_fields=["caption"])
    variant.platform_specific_caption = ""
    variant.save(update_fields=["platform_specific_caption"])
    saved, changed = services.save_caption(post.pk, variant.pk, user, "First caption")
    assert changed and saved.number == 1
    assert saved.document.versions.count() == 1
    second, _ = services.save_caption(post.pk, variant.pk, user, "Second caption", base_version_id=saved.pk)
    assert second.number == 2
    account = create_manual_channel(workspace, "youtube", "Original caption channel")
    other = PlatformPost.objects.create(post=post, social_account=account, platform_specific_caption="Original caption")
    result, _ = services.save_caption(post.pk, other.pk, user, "New caption")
    assert result.number == 2
    assert TextDocument.objects.get(platform_post=other).versions.get(number=1).text == "Original caption"
