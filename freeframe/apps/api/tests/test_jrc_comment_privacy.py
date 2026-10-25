"""Team-only (internal) comments never reach a JR Content client or viewer (F7-B02).

JR Content maps Brightbean's client to the FreeFrame project role `reviewer` and its viewer to
`viewer`; team roles become `editor`. A client comments in public only, and every route that
returns or changes a comment answers as if an internal one did not exist.
"""
import uuid

import pytest
from fastapi import HTTPException

import apps.api.routers.comments as comments_module
import apps.api.routers.notifications as notifications_module
from apps.api.models.activity import Notification
from apps.api.schemas.comment import CommentCreate, CommentUpdate


@pytest.fixture
def world(real_db, monkeypatch):
    from apps.api.models.asset import Asset, AssetType, AssetVersion, ProcessingStatus
    from apps.api.models.comment import Comment
    from apps.api.models.project import Project, ProjectMember, ProjectRole, ProjectType
    from apps.api.models.user import User

    monkeypatch.setattr(comments_module, "send_task_safe", lambda *a, **k: None)
    monkeypatch.setattr(comments_module.event_service, "publish_sync", lambda *a, **k: None)

    def person(name, role, managed=True):
        user = User(email=f"{name}-{uuid.uuid4()}@t.local", name=name, jrc_managed=managed)
        real_db.add(user)
        real_db.flush()
        return user, role

    team, _ = person("team", ProjectRole.editor)
    client, _ = person("client", ProjectRole.reviewer)
    viewer, _ = person("viewer", ProjectRole.viewer)
    native, _ = person("native", ProjectRole.reviewer, managed=False)
    project = Project(name="p", project_type=ProjectType.personal, created_by=team.id)
    real_db.add(project)
    real_db.flush()
    for user, role in ((team, ProjectRole.editor), (client, ProjectRole.reviewer), (viewer, ProjectRole.viewer),
                       (native, ProjectRole.reviewer)):
        real_db.add(ProjectMember(project_id=project.id, user_id=user.id, role=role))
    asset = Asset(project_id=project.id, name="reel", asset_type=AssetType.video, created_by=team.id)
    real_db.add(asset)
    real_db.flush()
    version = AssetVersion(asset_id=asset.id, version_number=1, processing_status=ProcessingStatus.ready,
                           created_by=team.id)
    real_db.add(version)
    real_db.flush()

    def comment(body, visibility, parent=None, author=None):
        c = Comment(asset_id=asset.id, version_id=version.id, author_id=(author or team).id, body=body,
                    visibility=visibility, parent_id=parent.id if parent else None, timecode_start=1.0)
        real_db.add(c)
        real_db.flush()
        return c

    public = comment("PUBLIC shorten it", "public")
    internal = comment("SECRET team talk", "internal")
    reply_under_internal = comment("SECRET public-looking reply", "public", parent=internal)
    internal_under_public = comment("SECRET internal reply", "internal", parent=public)
    public_reply = comment("PUBLIC reply", "public", parent=public)
    return dict(db=real_db, team=team, client=client, viewer=viewer, native=native, asset=asset, version=version,
                public=public, internal=internal, reply_under_internal=reply_under_internal,
                internal_under_public=internal_under_public, public_reply=public_reply, comment=comment)


def _flatten(nodes):
    for node in nodes:
        yield node
        yield from _flatten(node.replies)


def _list(world, user, visibility=None):
    return comments_module.list_comments(
        world["asset"].id, version_id=None, visibility=visibility, db=world["db"], current_user=user
    )


def _bodies(world, user, visibility=None):
    return {c.body for c in _flatten(_list(world, user, visibility))}


@pytest.mark.parametrize("who", ["client", "viewer"])
def test_listing_hides_internal_comments_and_their_replies(world, who):
    bodies = _bodies(world, world[who])
    assert bodies == {"PUBLIC shorten it", "PUBLIC reply"}
    assert _bodies(world, world[who], visibility="internal") == set()


def test_team_and_unmanaged_reviewer_still_see_everything(world):
    everything = {"PUBLIC shorten it", "PUBLIC reply", "SECRET team talk", "SECRET public-looking reply",
                  "SECRET internal reply"}
    assert _bodies(world, world["team"]) == everything
    assert _bodies(world, world["native"]) == everything  # plain FreeFrame accounts are unchanged


def _create(world, user, **fields):
    body = CommentCreate(version_id=world["version"].id, body=fields.pop("text", "new"), **fields)
    return comments_module.create_comment(world["asset"].id, body, db=world["db"], current_user=user)


def test_client_cannot_write_internal_comments_or_reply_into_internal_threads(world):
    client = world["client"]
    for kwargs in ({"visibility": "internal"}, {"parent_id": world["internal"].id},
                   {"parent_id": world["reply_under_internal"].id}):
        with pytest.raises(HTTPException) as exc:
            _create(world, client, **kwargs)
        assert exc.value.status_code in (400, 403)
    for parent, visibility, status in ((world["internal"], "public", 404), (world["public"], "internal", 403),
                                       (world["reply_under_internal"], "public", 404)):
        with pytest.raises(HTTPException) as exc:
            comments_module.reply_to_comment(
                world["asset"].id, parent.id,
                CommentCreate(version_id=world["version"].id, body="x", visibility=visibility),
                db=world["db"], current_user=client,
            )
        assert exc.value.status_code == status
    ok = _create(world, client, text="Cut at 0:17", timecode_start=17.0)
    assert ok.visibility == "public"
    reply = comments_module.reply_to_comment(
        world["asset"].id, world["public"].id, CommentCreate(version_id=world["version"].id, body="ok"),
        db=world["db"], current_user=client,
    )
    assert reply.visibility == "public"


def test_team_can_write_internal(world):
    assert _create(world, world["team"], visibility="internal").visibility == "internal"


def test_internal_comment_threads_are_not_broadcast_over_sse(world, monkeypatch):
    published = []
    monkeypatch.setattr(comments_module.event_service, "publish_sync", lambda *args: published.append(args))

    hidden = _create(world, world["team"], visibility="internal", text="private")
    public = _create(world, world["team"], text="visible")
    hidden_reply = comments_module.reply_to_comment(
        world["asset"].id, world["internal"].id,
        CommentCreate(version_id=world["version"].id, body="private reply", visibility="public"),
        db=world["db"], current_user=world["team"],
    )

    assert [args[2]["comment_id"] for args in published] == [str(public.id)]
    assert str(hidden.id) not in repr(published)
    assert str(hidden_reply.id) not in repr(published)


@pytest.mark.parametrize("hidden", ["internal", "reply_under_internal", "internal_under_public"])
def test_client_gets_404_for_internal_comments_on_every_route(world, hidden):
    db, client, target = world["db"], world["client"], world[hidden]

    def expect_404(call):
        with pytest.raises(HTTPException) as exc:
            call()
        assert exc.value.status_code == 404

    expect_404(lambda: comments_module.update_comment(target.id, CommentUpdate(body="x"), db=db, current_user=client))
    expect_404(lambda: comments_module.delete_comment(target.id, db=db, current_user=client))
    expect_404(lambda: comments_module.resolve_comment(target.id, db=db, current_user=client))
    expect_404(lambda: comments_module.list_reactions(target.id, db=db, current_user=client))
    expect_404(lambda: comments_module.comment_deep_link(world["asset"].id, target.id, db=db, current_user=client))
    assert target.deleted_at is None and target.resolved is not True


def test_client_still_reaches_public_comments_by_id(world):
    db, client = world["db"], world["client"]
    link = comments_module.comment_deep_link(world["asset"].id, world["public"].id, db=db, current_user=client)
    assert str(world["public"].id) in link["url"]
    assert comments_module.resolve_comment(world["public"].id, db=db, current_user=client).resolved is True


def test_csv_export_leaves_out_internal_comments_for_a_client(world):
    def export(user):
        return comments_module.export_comments(
            world["asset"].id, format="csv", version_id=world["version"].id, fps=None, start_tc=None,
            include_resolved=True, db=world["db"], current_user=user,
        ).body.decode("utf-8")

    client_csv, team_csv = export(world["client"]), export(world["team"])
    assert "PUBLIC shorten it" in client_csv and "SECRET" not in client_csv
    assert "SECRET team talk" in team_csv and "SECRET internal reply" in team_csv


def test_mentions_and_notifications_do_not_announce_internal_comments(world):
    db, client = world["db"], world["client"]
    _create(world, world["team"], visibility="internal", text="psst", mention_user_ids=[client.id])
    assert db.query(Notification).filter(Notification.user_id == client.id).count() == 0
    # internal reply under the client's own public comment: the author is not notified either
    mine = world["comment"]("Mine", "public", author=client)
    comments_module.reply_to_comment(
        world["asset"].id, mine.id,
        CommentCreate(version_id=world["version"].id, body="team only", visibility="internal"),
        db=db, current_user=world["team"],
    )
    assert db.query(Notification).filter(Notification.user_id == client.id).count() == 0
    # public ones still arrive
    comments_module.reply_to_comment(
        world["asset"].id, mine.id, CommentCreate(version_id=world["version"].id, body="thanks"),
        db=db, current_user=world["team"],
    )
    _create(world, world["team"], text="hello", mention_user_ids=[client.id])
    assert db.query(Notification).filter(Notification.user_id == client.id).count() == 2


def test_notification_feed_skips_old_internal_rows_for_a_client(world):
    from apps.api.models.activity import NotificationType

    db, client = world["db"], world["client"]
    for comment in (world["internal"], world["reply_under_internal"], world["public"]):
        db.add(Notification(user_id=client.id, type=NotificationType.comment, asset_id=world["asset"].id,
                            comment_id=comment.id))
    db.flush()
    feed = notifications_module.list_notifications(unread_only=False, db=db, current_user=client)
    assert [n.comment_preview for n in feed] == ["PUBLIC shorten it"]
