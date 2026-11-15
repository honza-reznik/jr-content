"""P2-B16: actual versions, role/privacy boundaries, search/trash and shared transports."""

from unittest.mock import patch

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.approvals import comments
from apps.approvals.models import PostComment
from apps.composer.models import Post
from apps.jrc_channels.services import create_manual_channel
from apps.jrc_production import shotlist
from apps.jrc_production.search import search_content
from apps.jrc_production.services import create_content
from apps.jrc_texts import services as texts
from apps.jrc_texts.models import TextDocument, TextVersion
from apps.members.models import WorkspaceMembership

pytestmark = pytest.mark.django_db


def rich(text):
    return {"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": text}]}]}


@pytest.fixture
def post(workspace, user):
    account = create_manual_channel(workspace, "instagram", "Version test")
    return create_content(workspace, "Version comments", user, social_account_ids=[account.pk])


def saved(post, user, kind="script", number=1, output=None):
    document, _ = TextDocument.objects.get_or_create(
        post=post,
        kind=kind,
        platform_post=output,
        defaults={"workspace": post.workspace, "format": "plain" if kind == "caption" else "rich"},
    )
    return TextVersion.objects.create(
        document=document,
        number=number,
        state="final",
        content={"text": f"Text V{number}"} if kind == "caption" else rich(f"Text V{number}"),
        text=f"Text V{number}",
        created_by=user,
        finalized_at=timezone.now(),
    )


def panel_url(post):
    return reverse("jrc_production:version_comments", args=[post.workspace_id, post.pk])


def version_url(comment):
    return reverse("jrc_production:comment_version", args=[comment.post.workspace_id, comment.post_id, comment.pk])


@pytest.mark.parametrize("kind", ["concept", "script", "caption", "shotlist"])
@pytest.mark.parametrize("role", ["owner", "manager", "editor", "contributor", "client", "viewer"])
def test_role_visibility_write_reply_and_version_read(client, workspace, post, user, kind, role):
    if kind == "shotlist":
        version = shotlist.snapshot(post)
        target = {"shotlist_version_id": version.pk}
        query = {"kind": kind, "format": version.output_format}
    else:
        output = post.platform_posts.first() if kind == "caption" else None
        # create_content has already created Concept V1
        version = saved(post, user, kind, number=2 if kind == "concept" else 1, output=output)
        target = {"text_version_id": version.pk}
        query = {"kind": kind, "output": str(output.pk) if output else ""}
    internal = comments.create_comment(post, user, "SECRET version note", **target)
    shared = comments.create_comment(post, user, "Visible version note", visibility="external", **target)
    actor = User.objects.create_user(email=f"{role}@example.invalid", name=role, tos_accepted_at=timezone.now())
    WorkspaceMembership.objects.create(workspace=workspace, user=actor, workspace_role=role)
    client.force_login(actor)
    response = client.get(panel_url(post), query)
    assert response.status_code == 200
    assert b"Visible version note" in response.content
    assert (b"SECRET version note" in response.content) == (role not in ("client", "viewer"))
    assert client.get(version_url(shared)).status_code == 200
    assert client.get(version_url(internal)).status_code == (404 if role in ("client", "viewer") else 200)
    data = {
        **query,
        "body": "Reply from role",
        "parent_id": str(shared.pk),
        **{k.removesuffix("_id"): str(v) for k, v in target.items()},
    }
    result = client.post(panel_url(post), data)
    assert result.status_code == (403 if role == "viewer" else 200)
    if role != "viewer":
        reply = PostComment.objects.get(body="Reply from role")
        assert reply.visibility == "external"
        assert comments.section_key(reply) == comments.section_key(shared)
    # viewer is read-only and clients cannot resolve threads.
    result = client.post(
        panel_url(post), {**query, "action": "resolve", "comment_id": str(shared.pk), "resolved": "true"}
    )
    assert result.status_code == (200 if role in ("owner", "manager") else 403)


def test_entire_section_keeps_older_versions_and_excludes_discussion(client, post, user):
    one = saved(post, user)
    two = saved(post, user, number=2)
    comments.create_comment(post, user, "Earlier version", text_version_id=one.pk)
    comments.create_comment(post, user, "Later version", text_version_id=two.pk)
    comments.create_comment(post, user, "General discussion")
    client.force_login(user)
    result = client.get(panel_url(post), {"kind": "script"})
    assert b"Earlier version" in result.content and b"Later version" in result.content
    assert b"General discussion" not in result.content
    detail = reverse("jrc_production:content_detail", args=[post.workspace_id, post.pk])
    discussion = client.get(detail, {"tab": "discussion"})
    assert b"General discussion" in discussion.content and b"Earlier version" not in discussion.content


@pytest.mark.parametrize("kind", ["script", "concept", "caption"])
def test_comment_pins_autosave_version_inside_fifteen_minutes(post, user, kind):
    output = post.platform_posts.first()

    def save(text, base=None, revision=0):
        if kind == "caption":
            return texts.autosave_caption(
                post.pk, output.pk, user, text, base_version_id=base, revision=revision, device="version-test-device"
            ).version
        return texts.autosave_text(
            post.pk,
            user,
            kind=kind,
            content=rich(text),
            base_version_id=base,
            revision=revision,
            device="version-test-device",
        ).version

    initial = TextVersion.objects.filter(document__post=post, document__kind=kind).order_by("-number").first()
    one = save("First", initial.pk if initial else None)
    amended = save("Before comment", one.pk, one.revision)
    assert amended.pk == one.pk
    note = comments.create_comment(
        post, user, "Pin exact content", text_version_id=one.pk, version_revision=amended.revision
    )
    next_version = save("After comment", amended.pk, amended.revision)
    assert next_version.pk != amended.pk
    note.refresh_from_db()
    assert "Before comment" in note.text_version.text
    assert "After comment" not in note.text_version.text
    with pytest.raises(ValueError, match="version changed"):
        comments.create_comment(post, user, "Stale browser", text_version_id=one.pk, version_revision=0)


def test_cross_post_section_and_output_injection_rejected(client, workspace, post, user):
    script = saved(post, user)
    caption = saved(post, user, "caption", output=post.platform_posts.first())
    other = Post.objects.create(workspace=workspace, author=user, title="Other")
    foreign = saved(other, user)
    with pytest.raises(ValueError):
        comments.create_comment(post, user, "Probe", text_version_id=foreign.pk)
    root = comments.create_comment(post, user, "Script root", text_version_id=script.pk)
    with pytest.raises(ValueError, match="section"):
        comments.create_comment(post, user, "Wrong section reply", parent_id=root.pk, text_version_id=caption.pk)
    client.force_login(user)
    for version in (foreign, caption):
        response = client.post(panel_url(post), {"kind": "script", "body": "Injected", "text_version": str(version.pk)})
        assert response.status_code in (400, 404)
    assert not PostComment.objects.filter(body="Injected").exists()


def test_shotlist_snapshot_survives_edit_reorder_delete_and_other_formats(client, post, user):
    item = shotlist.add_item(post, user, "Original shot")
    one = shotlist.snapshot(post)
    comment = comments.create_comment(post, user, "Keep this shot", shotlist_version_id=one.pk)
    shotlist.update_item(post, item.pk, user, text="Changed shot")
    two = shotlist.snapshot(post)
    assert two.pk != one.pk and two.number == one.number + 1
    shotlist.delete_item(post, item.pk, user)
    client.force_login(user)
    assert "Original shot" in client.get(version_url(comment)).json()["text"]
    assert "Changed shot" not in client.get(version_url(comment)).json()["text"]


def test_search_and_trash_use_same_visibility(client, workspace, post, user):
    version = saved(post, user)
    internal = comments.create_comment(post, user, "private-needle", text_version_id=version.pk)
    shared = comments.create_comment(post, user, "shared-needle", text_version_id=version.pk, visibility="external")
    actor = User.objects.create_user(email="reader@example.invalid")
    WorkspaceMembership.objects.create(workspace=workspace, user=actor, workspace_role="client")
    assert search_content([post], actor, "private-needle")[0] == []
    assert search_content([post], actor, "shared-needle")[0] == [post]
    assert search_content([post], user, "private-needle")[0] == [post]
    post.trashed_at = timezone.now()
    post.save(update_fields=["trashed_at"])
    assert comments.get_comments_for_post(post, user) == []
    client.force_login(user)
    assert client.get(panel_url(post), {"kind": "script"}).status_code == 404
    assert client.get(version_url(shared)).status_code == 404
    with pytest.raises(PermissionError):
        comments.create_comment(post, user, "Trash write", text_version_id=internal.text_version_id)


def test_mentions_follow_existing_channels_and_version_link(post, user, workspace):
    version = saved(post, user)
    actor = User.objects.create_user(email="mention@example.invalid", name="Mention")
    WorkspaceMembership.objects.create(workspace=workspace, user=actor, workspace_role="editor")
    with patch("apps.approvals.comments.notify") as notify:
        comments.create_comment(post, user, "@mention read this", text_version_id=version.pk)
    assert notify.call_count == 1
    assert "tab=script" in notify.call_args.kwargs["data"]["action_url"]
    assert str(version.pk) in notify.call_args.kwargs["data"]["action_url"]


def test_captions_and_shotlists_keep_outputs_and_formats_separate(client, post, user):
    from apps.jrc_production.services import add_content_channel

    first = post.platform_posts.first()
    second = add_content_channel(post, first.social_account_id, user, output_format="story")
    one = saved(post, user, "caption", output=first)
    two = saved(post, user, "caption", output=second)
    comments.create_comment(post, user, "First caption only", text_version_id=one.pk)
    comments.create_comment(post, user, "Second caption only", text_version_id=two.pk)
    item = shotlist.add_item(post, user, "First format shot", output_format=first.output_format)
    shotlist.add_item(post, user, "Second format shot", output_format=second.output_format)
    first_snapshot = shotlist.snapshot(post, first.output_format)
    second_snapshot = shotlist.snapshot(post, second.output_format)
    comments.create_comment(post, user, "First format comment", shotlist_version_id=first_snapshot.pk)
    comments.create_comment(post, user, "Second format comment", shotlist_version_id=second_snapshot.pk)
    client.force_login(user)
    for copy, visible, hidden in (
        (first, "First caption only", "Second caption only"),
        (second, "Second caption only", "First caption only"),
    ):
        response = client.get(panel_url(post), {"kind": "caption", "output": str(copy.pk)})
        assert visible.encode() in response.content and hidden.encode() not in response.content
    first_panel = client.get(panel_url(post), {"kind": "shotlist", "format": first.output_format})
    assert b"First format comment" in first_panel.content and b"Second format comment" not in first_panel.content
    # Each local change records a new immutable state, even before someone opens the comment panel.
    old_number = first_snapshot.number
    shotlist.update_item(post, item.pk, user, text="Edited first format")
    assert shotlist.snapshot(post, first.output_format).number == old_number + 1
    assert shotlist.snapshot(post, second.output_format).pk == second_snapshot.pk
    post.delete()  # RESTRICT still permits whole-content deletion; all its versions/comments are collected.
    assert not PostComment.objects.filter(text_version_id__in=[one.pk, two.pk]).exists()


def test_corrupt_cross_post_version_is_hidden_on_every_read_surface(client, workspace, post, user):
    other = Post.objects.create(workspace=workspace, author=user, title="Other content")
    version = saved(other, user)
    corrupt = PostComment.objects.create(post=post, author=user, body="Wrong document", text_version=version)
    assert not comments.can_view_comment(corrupt, user)
    assert comments.get_comments_for_post(post, user) == []
    client.force_login(user)
    assert client.get(version_url(corrupt)).status_code == 404


def test_first_output_adopts_existing_shotlist_conversation(client, workspace, user):
    from apps.jrc_production.services import add_content_channel

    post = create_content(workspace, "Shotlist before channels", user)
    shotlist.add_item(post, user, "Before the first output")
    original = shotlist.snapshot(post)
    note = comments.create_comment(post, user, "Conversation before outputs", shotlist_version_id=original.pk)
    account = create_manual_channel(workspace, "instagram", "First output")
    output = add_content_channel(post, account.pk, user)
    current = shotlist.snapshot(post, output.output_format)
    assert current.pk == original.pk
    original.refresh_from_db()
    assert original.output_format == output.output_format
    client.force_login(user)
    result = client.get(panel_url(post), {"kind": "shotlist", "format": output.output_format})
    assert b"Conversation before outputs" in result.content
    assert "Before the first output" in client.get(version_url(note)).json()["text"]
    reply = comments.create_comment(
        post, user, "Still the same thread", parent_id=note.pk, shotlist_version_id=current.pk
    )
    assert reply.shotlist_version_id == original.pk


@pytest.mark.parametrize("kind", ["discussion", "concept", "script", "caption", "shotlist"])
def test_client_notification_targets_comment_section(post, user, workspace, kind):
    from apps.notifications.models import Notification

    actor = User.objects.create_user(email="feedback@example.invalid", name="Feedback")
    WorkspaceMembership.objects.create(workspace=workspace, user=actor, workspace_role="client")
    target = {}
    if kind == "shotlist":
        target = {"shotlist_version_id": shotlist.snapshot(post).pk}
    elif kind != "discussion":
        version = saved(post, user, kind, number=2, output=post.platform_posts.first() if kind == "caption" else None)
        target = {"text_version_id": version.pk}
    comments.create_comment(post, actor, "Client feedback", **target)
    notice = Notification.objects.filter(user=user, event_type="content_client_comment").latest("created_at")
    assert f"tab={kind}" in notice.data["action_url"]
    assert ("Discussion" in notice.title) == (kind == "discussion")


@pytest.mark.parametrize("role", ["owner", "client", "viewer"])
def test_shotlist_get_neither_writes_nor_locks(client, post, user, workspace, role):
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    actor = user
    if role != "owner":
        actor = User.objects.create_user(email=f"read-{role}@example.invalid", tos_accepted_at=timezone.now())
        WorkspaceMembership.objects.create(workspace=workspace, user=actor, workspace_role=role)
    client.force_login(actor)
    assert post.shotlist_versions.count() == 0
    detail = reverse("jrc_production:content_detail", args=[workspace.pk, post.pk])
    with CaptureQueriesContext(connection) as captured:
        assert client.get(detail, {"tab": "shotlist"}).status_code == 200
        response = client.get(panel_url(post), {"kind": "shotlist"})
        assert response.status_code == 200
    assert post.shotlist_versions.count() == 0
    for query in captured:
        sql = query["sql"].upper()
        assert "FOR UPDATE" not in sql
        if "SHOTLIST" in sql:
            assert not sql.startswith(("INSERT", "UPDATE", "DELETE"))
    if role != "viewer":
        result = client.post(
            panel_url(post),
            {
                "kind": "shotlist",
                "body": "First comment without GET snapshot",
                "shotlist_state": response.context["comment_state"],
            },
        )
        assert result.status_code == 200
        assert post.shotlist_versions.count() == 1
        assert PostComment.objects.get(body="First comment without GET snapshot").shotlist_version_id


def test_first_shotlist_comment_rejects_changed_displayed_state(client, post, user):
    client.force_login(user)
    response = client.get(panel_url(post), {"kind": "shotlist"})
    state = response.context["comment_state"]
    shotlist.add_item(post, user, "Someone else's new shot")
    response = client.post(
        panel_url(post), {"kind": "shotlist", "body": "Stale first comment", "shotlist_state": state}
    )
    assert response.status_code == 400
    assert "Reload the list" in response.json()["error"]
    assert not PostComment.objects.filter(body="Stale first comment").exists()


def test_removed_output_snapshot_does_not_keep_empty_format_or_adopt_thumbnail(client, post, user):
    from apps.jrc_production.services import add_content_channel, remove_content_channel

    first = post.platform_posts.first()
    other = add_content_channel(post, first.social_account_id, user, output_format="story")
    old = shotlist.snapshot(post, first.output_format)
    comment = comments.create_comment(post, user, "Old format feedback", shotlist_version_id=old.pk)
    client.force_login(user)
    assert client.get(panel_url(post), {"kind": "shotlist", "format": first.output_format}).status_code == 200
    remove_content_channel(post, first.pk, user)
    assert shotlist.shotlist_formats(post) == [other.output_format]
    assert any(item.kind == "thumbnail" for item in shotlist.items_for(post, other.output_format))
    assert client.get(version_url(comment)).status_code == 200


def test_invalid_comment_revision_has_readable_error(client, post, user):
    version = saved(post, user)
    client.force_login(user)
    response = client.post(
        panel_url(post),
        {
            "kind": "script",
            "body": "Bad revision",
            "text_version": str(version.pk),
            "revision": "not-a-number",
        },
    )
    assert response.status_code == 400
    assert response.json() == {"error": "Choose a valid version revision."}
    assert not PostComment.objects.filter(body="Bad revision").exists()


def test_comment_serialization_queries_stay_constant_and_version_url_is_rest(post, user, django_assert_num_queries):
    from apps.api.routers.content import _comment_info

    version = saved(post, user)
    root = comments.create_comment(post, user, "API root", text_version_id=version.pk)
    for i in range(4):
        comments.create_comment(post, user, f"API reply {i}", parent_id=root.pk, text_version_id=version.pk)
    with django_assert_num_queries(2):
        visible = comments.get_comments_for_post(post, user)
        result = [_comment_info(comment) for comment in visible]
    data = result[0]
    assert data.version_url.endswith(f"/api/v1/posts/{post.pk}/comments/{root.pk}/version")
    assert len(data.replies) == 4
    assert all("/api/v1/posts/" in reply.version_url for reply in data.replies)


def test_shared_comment_labels_and_board_counts_respect_visibility(client, post, user, workspace):
    from django.template.loader import render_to_string

    from apps.jrc_production.views import _visible_comment_counts

    version = saved(post, user, number=3)
    shared = comments.create_comment(post, user, "Shared script", text_version_id=version.pk, visibility="external")
    hidden = saved(post, user, "caption", output=post.platform_posts.first())
    comments.create_comment(post, user, "Private caption", text_version_id=hidden.pk)
    actor = User.objects.create_user(email="label-reader@example.invalid")
    WorkspaceMembership.objects.create(workspace=workspace, user=actor, workspace_role="client")
    assert _visible_comment_counts([post], actor) == {post.pk: 1}
    assert post.comment_version_labels == ["Script · V3"]
    post.visible_comments = [shared]
    post.client_pending = True
    for template in ("client_portal/_client_card.html", "approvals/partials/_approval_row.html"):
        html = render_to_string(template, {"post": post, "workspace": workspace})
        assert "Script · V3" in html and "Shared script" in html
        assert "Caption · V1" not in html


def test_readded_format_appends_unformatted_history_without_losing_comments(client, post, user):
    from apps.jrc_production.services import add_content_channel, remove_content_channel

    output = post.platform_posts.first()
    original = shotlist.snapshot(post)
    original_comment = comments.create_comment(post, user, "Original format", shotlist_version_id=original.pk)
    remove_content_channel(post, output.pk, user)
    unformatted = shotlist.snapshot(post)
    second_comment = comments.create_comment(post, user, "Without outputs", shotlist_version_id=unformatted.pk)
    content_before = unformatted.content
    add_content_channel(post, output.social_account_id, user, output_format=output.output_format)
    original.refresh_from_db()
    unformatted.refresh_from_db()
    assert original.number == 1
    assert unformatted.number == 2
    assert original.output_format == unformatted.output_format == output.output_format
    assert unformatted.content == content_before
    assert shotlist.snapshot(post, output.output_format).pk == unformatted.pk
    client.force_login(user)
    panel = client.get(panel_url(post), {"kind": "shotlist", "format": output.output_format})
    assert b"Original format" in panel.content and b"Without outputs" in panel.content
    for comment in (original_comment, second_comment):
        assert client.get(version_url(comment)).json()["text"] == "☐ Thumbnail photo"
