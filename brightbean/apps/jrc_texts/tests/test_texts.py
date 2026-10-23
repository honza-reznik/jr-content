from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, close_old_connections, connection, transaction

from apps.accounts.models import User
from apps.composer.models import Post
from apps.jrc_texts import services
from apps.jrc_texts.models import TextDocument, TextVersion
from apps.members.models import CustomRole, WorkspaceMembership
from apps.workspaces.models import Workspace

pytestmark = pytest.mark.django_db


@pytest.fixture
def post(workspace, user):
    return Post.objects.create(workspace=workspace, author=user, title="Script test")


@pytest.fixture
def document(post, user):
    return services.create_document(post.pk, user, markdown="# Scénář\n\nPrvní verze.")


def base_version(document):
    return document.versions.get(state="final")


def test_edit_save_and_read_history(document, user):
    base = base_version(document)
    draft = services.edit(base.pk, user)
    assert draft.pk != base.pk
    assert draft.number == 2 and draft.based_on_id == base.pk
    assert draft.content == base.content
    assert draft.draft_owner == user and draft.created_by == user
    assert draft.draft_heartbeat_at is not None
    saved = services.save(draft.pk, user, expected_revision=0, markdown="# Nový scénář")
    assert saved.pk == draft.pk and saved.state == "final"
    assert saved.draft_owner is None and saved.finalized_at is not None
    assert services.latest_version(document.pk, user).pk == saved.pk
    assert services.get_version(base.pk, user).content == base.content
    assert services.get_version(saved.pk, user).text == "# Nový scénář"


def test_discard_and_no_change_save(document, user):
    base = base_version(document)
    first = services.edit(base.pk, user)
    services.discard(first.pk, user, expected_revision=0)
    second = services.edit(base.pk, user)
    assert second.pk != first.pk and second.number == first.number
    result = services.save(second.pk, user, expected_revision=0, content=base.content)
    assert result.pk == base.pk
    assert document.versions.count() == 1


@pytest.mark.parametrize("operation", ["save", "discard", "update_draft"])
def test_old_tab_cannot_change_replacement_draft(document, user, operation):
    base = base_version(document)
    old = services.edit(base.pk, user)
    services.discard(old.pk, user, expected_revision=0)
    current = services.edit(base.pk, user)
    kwargs = {} if operation == "discard" else {"markdown": "Stará karta"}
    with pytest.raises(services.ConflictError) as exc:
        getattr(services, operation)(old.pk, user, expected_revision=0, **kwargs)
    assert exc.value.status_code == 409
    assert services.get_version(current.pk, user).content == base.content


@pytest.mark.parametrize("operation", ["save", "discard", "update_draft"])
def test_two_tabs_with_same_draft_need_current_revision(document, user, operation):
    draft = services.edit(base_version(document).pk, user)
    changed = services.update_draft(draft.pk, user, expected_revision=0, markdown="Průběžně uložená změna")
    assert changed.revision == 1
    kwargs = {} if operation == "discard" else {"markdown": "Zastaralá změna"}
    with pytest.raises(services.ConflictError):
        getattr(services, operation)(draft.pk, user, expected_revision=0, **kwargs)
    assert services.get_version(draft.pk, user).text == "Průběžně uložená změna"
    saved = services.save(draft.pk, user, expected_revision=1, content=changed.content)
    assert saved.state == "final"


@pytest.mark.parametrize("operation", ["edit", "save", "discard", "update_draft"])
def test_saved_or_stale_version_conflicts(document, user, operation):
    base = base_version(document)
    draft = services.edit(base.pk, user)
    services.save(draft.pk, user, expected_revision=0, markdown="Druhá verze")
    kwargs = {} if operation == "edit" else {"expected_revision": 0}
    if operation in {"save", "update_draft"}:
        kwargs["markdown"] = "Nesmí přepsat"
    with pytest.raises(services.ConflictError):
        getattr(services, operation)(base.pk, user, **kwargs)


@pytest.mark.parametrize("operation", ["get_version", "save", "discard", "update_draft"])
def test_another_member_cannot_mutate_owned_draft(document, user, workspace, operation):
    other = User.objects.create_user(email="other-texts@example.com", password="test-only")
    WorkspaceMembership.objects.create(workspace=workspace, user=other, workspace_role="owner")
    draft = services.edit(base_version(document).pk, user)
    kwargs = {} if operation == "get_version" else {"expected_revision": 0}
    if operation in {"save", "update_draft"}:
        kwargs["markdown"] = "Foreign change"
    with pytest.raises(PermissionDenied):
        getattr(services, operation)(draft.pk, other, **kwargs)
    with pytest.raises(services.ConflictError):
        services.edit(base_version(document).pk, other)


@pytest.mark.parametrize("scenario", ["outsider", "archived", "anonymous", "inactive", "viewer", "custom_viewer"])
def test_write_permissions(document, user, workspace, scenario):
    actor = user
    membership = WorkspaceMembership.objects.get(workspace=workspace, user=user)
    if scenario == "outsider":
        membership.delete()
    elif scenario == "archived":
        workspace.is_archived = True
        workspace.save()
    elif scenario == "anonymous":
        actor = AnonymousUser()
    elif scenario == "inactive":
        user.is_active = False
        user.save()
    elif scenario == "viewer":
        membership.workspace_role = "viewer"
        membership.save()
    else:
        membership.custom_role = CustomRole.objects.create(
            organization=workspace.organization, name="No text writes", permissions={}
        )
        membership.workspace_role = "client"
        membership.save()
    with pytest.raises(PermissionDenied):
        services.edit(base_version(document).pk, actor)
    assert document.versions.count() == 1


def test_client_can_read_own_workspace_but_cannot_edit_or_access_foreign_text(document, user, workspace, organization):
    base = base_version(document)
    draft = services.edit(base.pk, user)
    membership = WorkspaceMembership.objects.get(workspace=workspace, user=user)
    membership.workspace_role = "client"
    membership.save()
    assert services.get_version(base.pk, user).pk == base.pk
    assert services.latest_version(document.pk, user).pk == base.pk
    with pytest.raises(PermissionDenied):
        services.edit(base.pk, user)
    with pytest.raises(PermissionDenied):
        services.save(draft.pk, user, expected_revision=0, markdown="Klientská změna")
    foreign = Workspace.objects.create(organization=organization, name="Foreign")
    foreign_post = Post.objects.create(workspace=foreign, author=user, title="Secret")
    with pytest.raises(PermissionDenied):
        services.create_document(foreign_post.pk, user, markdown="Forbidden")
    # Removing membership from the current workspace forbids reads of every saved version too.
    membership.delete()
    with pytest.raises(PermissionDenied):
        services.get_version(draft.pk, user)
    with pytest.raises(PermissionDenied):
        services.latest_version(document.pk, user)


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE jrc_texts_textversion SET text = 'overwritten' WHERE id = %s",
        "UPDATE jrc_texts_textversion SET state = 'draft', finalized_at = NULL WHERE id = %s",
        "UPDATE jrc_texts_textversion SET content = '{}'::jsonb WHERE id = %s",
        "UPDATE jrc_texts_textversion SET created_by_id = NULL WHERE id = %s",
        "DELETE FROM jrc_texts_textversion WHERE id = %s",
    ],
)
def test_final_version_is_immutable_even_with_direct_sql(document, statement):
    version = base_version(document)
    assert connection.vendor == "postgresql"
    with (
        pytest.raises(IntegrityError, match="Saved text versions"),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute(statement, [version.pk])
        cursor.execute("SET CONSTRAINTS jrc_texts_protect_final IMMEDIATE")
    version.refresh_from_db()
    assert version.state == "final" and version.text


@pytest.mark.parametrize("violation", ["duplicate_number", "second_draft", "zero_number", "invalid_state", "source"])
def test_database_version_constraints(document, user, violation):
    base = base_version(document)
    draft = services.edit(base.pk, user)
    values = {"document": document, "number": 3, "content": base.content, "text": base.text}
    if violation == "duplicate_number":
        values.update(number=draft.number, state="final", finalized_at=base.finalized_at)
    elif violation == "zero_number":
        values.update(number=0, state="final", finalized_at=base.finalized_at)
    elif violation == "invalid_state":
        values.update(state="other")
    elif violation == "source":
        values.update(state="final", finalized_at=base.finalized_at, source="other")
    with pytest.raises(IntegrityError), transaction.atomic():
        TextVersion.objects.create(**values)


def test_document_constraints_and_wrong_channel(document, user, post, workspace, organization):
    with pytest.raises(ValidationError):
        services.create_document(post.pk, user, markdown="Duplicate script")
    with pytest.raises(IntegrityError), transaction.atomic():
        TextDocument.objects.create(post=post, workspace=workspace, kind="caption")
    from apps.composer.models import PlatformPost
    from apps.jrc_channels.services import create_manual_channel

    channel = create_manual_channel(workspace, "instagram", "Test")
    other_post = Post.objects.create(workspace=workspace, author=user, title="Other")
    variant = PlatformPost.objects.create(post=other_post, social_account=channel)
    with pytest.raises(ValidationError, match="must belong"):
        services.create_document(post.pk, user, kind="caption", platform_post_id=variant.pk, markdown="Wrong parent")


def test_plain_document_roundtrip_and_invalid_content(post, user):
    document = services.create_document(post.pk, user, format="plain", content={"text": "Emoji 👩‍💻\n**literal**"})
    base = base_version(document)
    assert base.content == {"text": base.text}
    draft = services.edit(base.pk, user)
    with pytest.raises(ValidationError):
        services.save(draft.pk, user, expected_revision=0, content={"text": 42})
    assert services.get_version(draft.pk, user).revision == 0


@pytest.mark.django_db(transaction=True)
def test_simultaneous_edit_creates_one_draft(document, user):
    base = base_version(document)
    barrier = Barrier(2)

    def start():
        close_old_connections()
        try:
            barrier.wait(timeout=10)
            try:
                return services.edit(base.pk, user).pk
            except services.ConflictError:
                return "conflict"
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: start(), range(2)))
    assert results.count("conflict") == 1
    assert document.versions.filter(state="draft").count() == 1


@pytest.mark.django_db(transaction=True)
def test_sql_delete_is_rejected_at_commit(document):
    version = base_version(document)
    with (
        pytest.raises(IntegrityError, match="Saved text versions"),
        transaction.atomic(),
        connection.cursor() as cursor,
    ):
        cursor.execute("DELETE FROM jrc_texts_textversion WHERE id = %s", [version.pk])
    assert TextVersion.objects.filter(pk=version.pk).exists()


@pytest.mark.django_db(transaction=True)
def test_whole_post_deletion_can_cascade_history(document, post, user):
    base = base_version(document)
    draft = services.edit(base.pk, user)
    services.save(draft.pk, user, expected_revision=0, markdown="Second")
    with transaction.atomic():
        post.delete()
    assert not TextDocument.objects.exists()
    assert not TextVersion.objects.exists()


@pytest.mark.django_db(transaction=True)
def test_deleting_author_preserves_saved_history(document, user, post):
    version = base_version(document)
    Post.objects.filter(pk=post.pk).update(author=None)
    with transaction.atomic():
        user.delete()
    version.refresh_from_db()
    assert version.created_by is None
    assert version.state == "final" and version.text


@pytest.mark.django_db(transaction=True)
def test_simultaneous_save_accepts_one_snapshot(document, user):
    draft = services.edit(base_version(document).pk, user)
    barrier = Barrier(2)

    def write(text):
        close_old_connections()
        try:
            barrier.wait(timeout=10)
            try:
                return services.save(draft.pk, user, expected_revision=0, markdown=text).text
            except services.ConflictError:
                return "conflict"
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(write, ["First tab", "Second tab"]))
    assert results.count("conflict") == 1
    assert document.versions.filter(state="final").count() == 2
    assert services.latest_version(document.pk, user).text in {"First tab", "Second tab"}


@pytest.mark.parametrize("source", ["api", "mcp"])
def test_api_sources_use_same_base_and_draft_rules(document, user, source):
    base = base_version(document)
    draft = services.edit(base.pk, user, source=source)
    assert draft.source == source
    with pytest.raises(services.ConflictError):
        services.edit(base.pk, user, source=source)
    saved = services.save(draft.pk, user, expected_revision=0, markdown="API version")
    assert saved.source == source
    with pytest.raises(services.ConflictError):
        services.edit(base.pk, user, source=source)


@pytest.mark.django_db(transaction=True)
def test_deleting_parent_cannot_hide_relocation_of_saved_version(document, user, workspace):
    version = base_version(document)
    other_post = Post.objects.create(workspace=workspace, author=user, title="Other text")
    other_document = services.create_document(other_post.pk, user, markdown="Other")
    with pytest.raises(IntegrityError, match="Saved text versions"), transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE jrc_texts_textversion SET document_id = %s, number = 2 WHERE id = %s",
                [other_document.pk, version.pk],
            )
        document.delete()
    version.refresh_from_db()
    assert version.document_id != other_document.pk
