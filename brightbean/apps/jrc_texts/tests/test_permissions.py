from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import close_old_connections

from apps.accounts.models import User
from apps.composer.models import Post
from apps.jrc_texts import services
from apps.jrc_texts.models import TextVersion
from apps.members.models import CustomRole, WorkspaceMembership
from apps.workspaces.models import Workspace

pytestmark = pytest.mark.django_db


@pytest.fixture
def document(workspace, user):
    post = Post.objects.create(workspace=workspace, author=user, title="Permissions test")
    return services.create_document(post.pk, user, format="plain", markdown="Saved text")


def set_role(workspace, user, role, permissions=None):
    membership = WorkspaceMembership.objects.get(workspace=workspace, user=user)
    membership.workspace_role = role
    membership.custom_role = (
        CustomRole.objects.create(organization=workspace.organization, name="Text test role", permissions=permissions)
        if permissions is not None
        else None
    )
    membership.save()


@pytest.mark.parametrize(
    ("role", "permissions", "can_write", "can_edit_others"),
    [
        ("owner", None, True, True),
        ("manager", None, True, True),
        ("editor", None, True, True),
        ("contributor", None, True, False),
        ("client", None, False, False),
        ("viewer", None, False, False),
        ("owner", {"create_posts": True}, True, False),
        ("client", {"create_posts": True, "edit_others_posts": True}, True, True),
        ("owner", {"edit_others_posts": True}, False, True),
    ],
)
@pytest.mark.parametrize("own_post", [True, False])
@pytest.mark.parametrize("operation", ["create_document", "edit", "save", "discard", "update_draft"])
def test_all_writers_follow_composer_permissions(
    document, workspace, user, role, permissions, can_write, can_edit_others, own_post, operation
):
    base = document.versions.get(state="final")
    draft = services.edit(base.pk, user) if operation in {"save", "discard", "update_draft"} else None
    # Recheck permissions even when a draft was opened before a role/author change.
    if not own_post:
        other = User.objects.create_user(email="post-author@example.com")
        Post.objects.filter(pk=document.post_id).update(author=other)
    set_role(workspace, user, role, permissions)
    before = list(document.versions.values())

    def write():
        if operation == "create_document":
            post = Post.objects.create(
                workspace=workspace, author_id=user.pk if own_post else other.pk, title="New text"
            )
            return services.create_document(post.pk, user, format="plain", markdown="New")
        if operation == "edit":
            return services.edit(base.pk, user)
        kwargs = {} if operation == "discard" else {"markdown": "Changed"}
        return getattr(services, operation)(draft.pk, user, expected_revision=0, **kwargs)

    if can_write and (own_post or can_edit_others):
        write()
    else:
        with pytest.raises(PermissionDenied):
            write()
        assert list(document.versions.values()) == before
    assert services.get_version(base.pk, user).pk == base.pk


@pytest.fixture(params=["deleted", "membership_removed"])
def orphan(request, document, workspace):
    editor = User.objects.create_user(email="departing-editor@example.com")
    membership = WorkspaceMembership.objects.create(workspace=workspace, user=editor, workspace_role="editor")
    base = document.versions.get(state="final")
    draft = services.edit(base.pk, editor)
    draft = services.update_draft(draft.pk, editor, expected_revision=0, markdown="Unfinished work")
    if request.param == "deleted":
        editor.delete()
    else:
        membership.delete()
    draft.refresh_from_db()
    return draft


@pytest.mark.parametrize("role", ["owner", "manager", "custom_admin"])
@pytest.mark.parametrize("operation", ["discard", "edit"])
def test_administrator_can_recover_orphan(document, orphan, workspace, user, role, operation):
    permissions = None
    if role == "custom_admin":
        role = "viewer"
        permissions = {"create_posts": True, "edit_others_posts": True, "manage_workspace_settings": True}
    set_role(workspace, user, role, permissions)
    base = document.versions.get(state="final")
    if operation == "discard":
        services.discard(orphan.pk, user, expected_revision=orphan.revision)
        replacement = services.edit(base.pk, user)
    else:
        replacement = services.edit(base.pk, user)
    assert not TextVersion.objects.filter(pk=orphan.pk).exists()
    assert replacement.pk != orphan.pk and replacement.number == orphan.number
    assert replacement.draft_owner_id == user.pk and replacement.revision == 0
    assert replacement.content == base.content
    assert services.get_version(base.pk, user).text == "Saved text"
    with pytest.raises(services.ConflictError):
        services.save(orphan.pk, user, expected_revision=orphan.revision, markdown="Stale tab")
    saved = services.save(replacement.pk, user, expected_revision=0, markdown="Recovered text")
    assert saved.state == "final"


@pytest.mark.parametrize("role", ["editor", "contributor", "custom_editor"])
def test_non_administrator_cannot_recover_orphan(document, orphan, workspace, user, role):
    permissions = None
    if role == "custom_editor":
        # A custom role must not inherit administrative power from its fallback role.
        role = "owner"
        permissions = {"create_posts": True, "edit_others_posts": True}
    set_role(workspace, user, role, permissions)
    with pytest.raises(PermissionDenied):
        services.discard(orphan.pk, user, expected_revision=orphan.revision)
    with pytest.raises(services.ConflictError):
        services.edit(document.versions.get(state="final").pk, user)
    orphan.refresh_from_db()
    assert orphan.text == "Unfinished work"


@pytest.mark.parametrize("operation", ["save", "update_draft", "get_version"])
def test_administrator_cannot_take_over_or_read_orphan(orphan, user, operation):
    kwargs = {} if operation == "get_version" else {"expected_revision": orphan.revision, "markdown": "Taken over"}
    with pytest.raises(PermissionDenied):
        getattr(services, operation)(orphan.pk, user, **kwargs)
    orphan.refresh_from_db()
    assert orphan.text == "Unfinished work"


def test_orphan_recovery_keeps_revision_check_and_rolls_back_invalid_edit(document, orphan, user):
    with pytest.raises(services.ConflictError):
        services.discard(orphan.pk, user, expected_revision=orphan.revision - 1)
    with pytest.raises(ValidationError):
        services.edit(document.versions.get(state="final").pk, user, source="invalid")
    orphan.refresh_from_db()
    assert orphan.text == "Unfinished work"
    services.discard(orphan.pk, user, expected_revision=orphan.revision)


@pytest.mark.parametrize("orphan", ["membership_removed"], indirect=True)
def test_membership_in_another_workspace_does_not_keep_draft_owned(orphan, workspace, user):
    other = Workspace.objects.create(organization=workspace.organization, name="Other client")
    WorkspaceMembership.objects.create(workspace=other, user_id=orphan.draft_owner_id, workspace_role="owner")
    services.discard(orphan.pk, user, expected_revision=orphan.revision)
    assert not TextVersion.objects.filter(pk=orphan.pk).exists()


@pytest.mark.django_db(transaction=True)
def test_simultaneous_orphan_recovery_creates_one_replacement(document, orphan, user):
    base = document.versions.get(state="final")
    barrier = Barrier(2)

    def recover():
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
        results = list(pool.map(lambda _: recover(), range(2)))
    assert results.count("conflict") == 1
    replacement = document.versions.get(state="draft")
    assert replacement.pk != orphan.pk and replacement.draft_owner_id == user.pk
    assert replacement.content == base.content
