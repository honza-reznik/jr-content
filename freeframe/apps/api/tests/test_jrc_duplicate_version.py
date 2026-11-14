"""JR Content P2-B15: a converted version used for another output shares its stored files.

Covers the new endpoint (shared keys, no storage call, numbering, roles, refusals), the optional
comment copy (threads, visibility, authors, times, resolved state, drawings, reactions, attachments)
and every place that deletes stored objects: an object goes only with the last row naming it.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

import apps.api.routers.comments as comments_module
import apps.api.tasks.cleanup_tasks as ct
from apps.api.config import settings
from apps.api.models.activity import ActivityLog, Mention, Notification
from apps.api.models.asset import Asset, AssetType, AssetVersion, FileType, MediaFile, ProcessingStatus
from apps.api.models.comment import Annotation, Comment, CommentAttachment, CommentReaction
from apps.api.models.project import Project, ProjectMember, ProjectRole, ProjectType
from apps.api.models.user import GuestUser, User
from apps.api.routers import jrc_versions
from apps.api.routers.files import authorize_file
from apps.api.services import s3_service, storage
from apps.api.services.jrc_duplicate import duplicate_version

OLD = datetime(2026, 11, 1, 9, 30, tzinfo=timezone.utc)


@pytest.fixture
def world(real_db, monkeypatch):
    # The copy must never touch storage: any call fails the test.
    def forbidden(*args, **kwargs):
        raise AssertionError("storage must not be called when a version is shared")

    for name in ("copy_object", "put_object", "delete_object", "delete_prefix", "create_multipart_upload"):
        if hasattr(s3_service, name):
            monkeypatch.setattr(s3_service, name, forbidden)
    db = real_db

    def person(name, managed=True):
        user = User(email=f"{name}-{uuid.uuid4()}@t.local", name=name, jrc_managed=managed)
        db.add(user)
        db.flush()
        return user

    team, client, viewer, outsider = person("team"), person("client"), person("viewer"), person("outsider")
    project = Project(name="Ella", project_type=ProjectType.personal, created_by=team.id)
    other_project = Project(name="Other", project_type=ProjectType.personal, created_by=outsider.id)
    db.add_all([project, other_project])
    db.flush()
    for user, role in ((team, ProjectRole.editor), (client, ProjectRole.reviewer), (viewer, ProjectRole.viewer)):
        db.add(ProjectMember(project_id=project.id, user_id=user.id, role=role))
    db.add(ProjectMember(project_id=other_project.id, user_id=outsider.id, role=ProjectRole.editor))
    db.add(ProjectMember(project_id=other_project.id, user_id=team.id, role=ProjectRole.editor))

    def asset(name, proj=project):
        row = Asset(project_id=proj.id, name=name, asset_type=AssetType.video, created_by=team.id)
        db.add(row)
        db.flush()
        return row

    def version(of, number=1, status=ProcessingStatus.ready, processed=True):
        row = AssetVersion(asset_id=of.id, version_number=number, processing_status=status, created_by=team.id)
        db.add(row)
        db.flush()
        base = f"{of.project_id}/{of.id}/{row.id}"
        db.add(MediaFile(
            version_id=row.id, file_type=FileType.video, original_filename="reel.mov", mime_type="video/quicktime",
            file_size_bytes=1000, s3_key_raw=f"raw/{base}/original.mov",
            s3_key_processed=f"processed/{base}" if processed else None,
            s3_key_download=f"processed/{base}/download.mp4" if processed else None,
            s3_key_thumbnail=f"processed/{base}/thumb.jpg" if processed else None,
            width=1080, height=1920, duration_seconds=12.5, fps=30.0,
        ))
        db.flush()
        return row

    source_asset = asset("Content · YouTube · Video")
    source = version(source_asset, 1)
    return dict(db=db, team=team, client=client, viewer=viewer, outsider=outsider, project=project,
                other_project=other_project, asset=asset, version=version, source_asset=source_asset,
                source=source)


def _media(db, version_id):
    return db.query(MediaFile).filter(MediaFile.version_id == version_id).one()


KEYS = ("s3_key_raw", "s3_key_processed", "s3_key_download", "s3_key_thumbnail")


def test_new_output_gets_a_ready_version_naming_the_same_files(world):
    db = world["db"]
    result = jrc_versions.duplicate(
        world["source"].id,
        jrc_versions.DuplicateVersionRequest(asset_name="Content · YouTube · Shorts"),
        db=db, current_user=world["team"],
    )
    target = db.query(Asset).filter(Asset.id == result["asset_id"]).one()
    assert target.project_id == world["project"].id and target.name == "Content · YouTube · Shorts"
    assert target.asset_type == AssetType.video and target.id != world["source_asset"].id
    version = db.query(AssetVersion).filter(AssetVersion.id == result["version_id"]).one()
    assert version.version_number == 1 == result["version_number"]
    assert version.processing_status == ProcessingStatus.ready  # no second conversion
    assert version.created_by == world["team"].id
    copy, original = _media(db, version.id), _media(db, world["source"].id)
    for column in KEYS + ("original_filename", "file_size_bytes", "width", "height", "duration_seconds", "fps"):
        assert getattr(copy, column) == getattr(original, column), column
    assert copy.id != original.id
    assert result["comments_copied"] == 0
    log = db.query(ActivityLog).filter(ActivityLog.asset_id == target.id).one()
    assert log.action == "jrc_version_duplicated"
    assert log.payload["source_version_id"] == str(world["source"].id)


def test_existing_output_gets_the_next_version_number(world):
    db = world["db"]
    target = world["asset"]("Content · Instagram · Reel")
    world["version"](target, 1)
    world["version"](target, 2)
    result = duplicate_version(db, world["team"], world["source"].id, target_asset_id=target.id)
    assert result["asset_id"] == target.id and result["version_number"] == 3
    again = duplicate_version(db, world["team"], world["source"].id, target_asset_id=target.id)
    assert again["version_number"] == 4  # the same source may be used twice; each is its own version


@pytest.mark.parametrize("who", ["client", "viewer", "outsider"])
def test_only_project_editors_may_use_a_version_for_another_output(world, who):
    with pytest.raises(HTTPException) as exc:
        duplicate_version(world["db"], world[who], world["source"].id, asset_name="x")
    assert exc.value.status_code == 403
    assert world["db"].query(Asset).filter(Asset.name == "x").count() == 0


def test_refusals(world):
    db, team = world["db"], world["team"]
    converting = world["version"](world["source_asset"], 2, status=ProcessingStatus.processing, processed=False)
    other = world["asset"]("Other client video", proj=world["other_project"])
    cases = [
        (dict(source_version_id=uuid.uuid4(), asset_name="x"), 404),
        (dict(source_version_id=converting.id, asset_name="x"), 409),
        (dict(source_version_id=world["source"].id, target_asset_id=other.id), 400),
        (dict(source_version_id=world["source"].id, target_asset_id=world["source_asset"].id), 400),
        (dict(source_version_id=world["source"].id, target_asset_id=uuid.uuid4()), 404),
        (dict(source_version_id=world["source"].id, asset_name="  "), 422),
    ]
    for kwargs, status in cases:
        source_id = kwargs.pop("source_version_id")
        with pytest.raises(HTTPException) as exc:
            duplicate_version(db, team, source_id, **kwargs)
        assert exc.value.status_code == status, kwargs
    world["source"].deleted_at = datetime.now(timezone.utc)
    db.flush()
    with pytest.raises(HTTPException) as exc:
        duplicate_version(db, team, world["source"].id, asset_name="x")
    assert exc.value.status_code == 404


@pytest.fixture
def thread(world):
    """Comments on the source: public and team-only threads, a guest, a drawing, a reaction, a file."""
    db, team, client = world["db"], world["team"], world["client"]
    guest = GuestUser(email=f"guest-{uuid.uuid4()}@t.local", name="Guest")
    db.add(guest)
    db.flush()

    def comment(body, visibility="public", parent=None, author=team, guest_author=None, resolved=False, at=0):
        row = Comment(
            asset_id=world["source_asset"].id, version_id=world["source"].id, body=body, visibility=visibility,
            parent_id=parent.id if parent else None, author_id=None if guest_author else author.id,
            guest_author_id=guest_author.id if guest_author else None, timecode_start=1.5 + at,
            timecode_end=3.0 + at, resolved=resolved, created_at=OLD + timedelta(minutes=at),
            updated_at=OLD + timedelta(minutes=at, seconds=30),
        )
        db.add(row)
        db.flush()
        return row

    public = comment("Cut the intro", at=1, author=client)
    reply = comment("Done", parent=public, at=2, resolved=True)
    internal = comment("Team only: music licence", visibility="internal", at=3)
    internal_reply = comment("Ask the client later", visibility="internal", parent=internal, at=4)
    guest_note = comment("Guest says hi", guest_author=guest, at=5)
    gone = comment("Deleted remark", at=6)
    gone_reply = comment("Reply under a deleted remark", parent=gone, at=7)
    gone.deleted_at = OLD
    db.add(Annotation(comment_id=public.id, drawing_data={"shapes": [1, 2]}, frame_number=45))
    db.add(CommentAttachment(comment_id=public.id, file_type="image", s3_key="comment-attachments/frame.png",
                             original_filename="frame.png", file_size_bytes=9))
    db.add(CommentReaction(comment_id=public.id, user_id=team.id, emoji="👍"))
    db.add(Mention(comment_id=public.id, mentioned_user_id=team.id))
    db.flush()
    return dict(guest=guest, public=public, reply=reply, internal=internal, internal_reply=internal_reply,
                guest_note=guest_note, gone=gone, gone_reply=gone_reply)


def _copied(world, version_id):
    return {c.body: c for c in world["db"].query(Comment).filter(Comment.version_id == version_id).all()}


def test_keep_comments_copies_threads_visibility_authors_times_and_state(world, thread):
    db = world["db"]
    result = duplicate_version(db, world["team"], world["source"].id, asset_name="Shorts", copy_comments=True)
    copies = _copied(world, result["version_id"])
    assert set(copies) == {"Cut the intro", "Done", "Team only: music licence", "Ask the client later",
                           "Guest says hi"}  # a deleted comment and the replies it hides stay behind
    assert result["comments_copied"] == 5
    for body, copy in copies.items():
        original = next(v for v in thread.values() if getattr(v, "body", None) == body)
        assert copy.id != original.id and copy.asset_id == result["asset_id"]
        for column in ("author_id", "guest_author_id", "timecode_start", "timecode_end", "visibility", "resolved",
                       "created_at", "updated_at"):
            assert getattr(copy, column) == getattr(original, column), (body, column)
    assert copies["Done"].parent_id == copies["Cut the intro"].id
    assert copies["Ask the client later"].parent_id == copies["Team only: music licence"].id
    assert copies["Guest says hi"].guest_author_id == thread["guest"].id
    new_public = copies["Cut the intro"].id
    assert db.query(Annotation).filter(Annotation.comment_id == new_public).one().drawing_data == {"shapes": [1, 2]}
    assert db.query(CommentReaction).filter(CommentReaction.comment_id == new_public).one().emoji == "👍"
    attachment = db.query(CommentAttachment).filter(CommentAttachment.comment_id == new_public).one()
    assert attachment.s3_key == "comment-attachments/frame.png"  # the same object, not a copy
    # Copies notify nobody: no mention or notification rows for them.
    assert db.query(Mention).filter(Mention.comment_id == new_public).count() == 0
    assert db.query(Notification).filter(Notification.comment_id == new_public).count() == 0
    # The source is unchanged.
    assert len(_copied(world, world["source"].id)) == 7


def test_without_keep_comments_the_new_version_starts_clean(world, thread):
    result = duplicate_version(world["db"], world["team"], world["source"].id, asset_name="Shorts")
    assert _copied(world, result["version_id"]) == {}


def test_client_still_sees_only_public_copies(world, thread, monkeypatch):
    monkeypatch.setattr(comments_module.event_service, "publish_sync", lambda *a, **k: None)
    result = duplicate_version(world["db"], world["team"], world["source"].id, asset_name="Shorts",
                               copy_comments=True)

    def bodies(user):
        nodes = comments_module.list_comments(result["asset_id"], version_id=result["version_id"], visibility=None,
                                              db=world["db"], current_user=user)
        out, stack = set(), list(nodes)
        while stack:
            node = stack.pop()
            out.add(node.body)
            stack.extend(node.replies)
        return out

    assert bodies(world["client"]) == {"Cut the intro", "Done", "Guest says hi"}
    assert bodies(world["team"]) == {"Cut the intro", "Done", "Team only: music licence", "Ask the client later",
                                     "Guest says hi"}


def _deleted(monkeypatch):
    deleted = []
    monkeypatch.setattr(ct, "delete_object", lambda key: deleted.append(key))
    monkeypatch.setattr(ct, "delete_prefix", lambda key: deleted.append(key))
    return deleted


def test_purging_one_branch_keeps_the_files_of_the_other(world, monkeypatch):
    db = world["db"]
    result = duplicate_version(db, world["team"], world["source"].id, asset_name="Shorts")
    keys = [getattr(_media(db, world["source"].id), column) for column in KEYS]
    deleted = _deleted(monkeypatch)
    ct._purge_asset(db, world["source_asset"].id, ct.PurgeCounts())
    assert deleted == []  # the Shorts version still names every object
    assert db.query(AssetVersion).filter(AssetVersion.id == world["source"].id).count() == 0
    ct._purge_asset(db, result["asset_id"], ct.PurgeCounts())
    assert sorted(deleted) == sorted(keys)  # the last row takes the objects with it


def test_purging_the_copy_first_also_keeps_the_source(world, monkeypatch):
    db = world["db"]
    result = duplicate_version(db, world["team"], world["source"].id, asset_name="Shorts")
    deleted = _deleted(monkeypatch)
    ct._purge_version(db, result["version_id"], ct.PurgeCounts())
    assert deleted == []
    ct._purge_version(db, world["source"].id, ct.PurgeCounts())
    assert len(deleted) == 4


def test_soft_deleted_branch_still_holds_its_files_until_purged(world, monkeypatch):
    db = world["db"]
    result = duplicate_version(db, world["team"], world["source"].id, asset_name="Shorts")
    target = db.query(Asset).filter(Asset.id == result["asset_id"]).one()
    target.deleted_at = datetime.now(timezone.utc)
    db.flush()
    deleted = _deleted(monkeypatch)
    ct._purge_version(db, world["source"].id, ct.PurgeCounts())
    assert deleted == []  # restorable from the trash until the retention purge


def test_discarding_an_upload_never_takes_shared_files(world, monkeypatch):
    db = world["db"]
    result = duplicate_version(db, world["team"], world["source"].id, asset_name="Shorts")
    copy = db.query(AssetVersion).filter(AssetVersion.id == result["version_id"]).one()
    monkeypatch.setattr(ct, "abort_multipart_upload", lambda *a: None)
    deleted = _deleted(monkeypatch)
    ct._dispose_version_files(db, copy)
    assert deleted == []


def test_shared_comment_attachment_survives_deleting_one_copy(world, thread, monkeypatch):
    db = world["db"]
    first = duplicate_version(db, world["team"], world["source"].id, asset_name="Shorts", copy_comments=True)
    second = duplicate_version(db, world["team"], world["source"].id, target_asset_id=first["asset_id"],
                               copy_comments=True)
    key = "comment-attachments/frame.png"
    assert db.query(CommentAttachment).filter(CommentAttachment.s3_key == key).count() == 3
    deleted = _deleted(monkeypatch)
    ct._purge_comment(db, thread["public"].id, ct.PurgeCounts())
    assert key not in deleted
    copy = _copied(world, first["version_id"])["Cut the intro"]
    attachment = db.query(CommentAttachment).filter(CommentAttachment.comment_id == copy.id).one()
    removed = []
    monkeypatch.setattr(comments_module.s3_service, "delete_object", lambda k: removed.append(k))
    comments_module.delete_attachment(copy.id, attachment.id, db=db, current_user=world["team"])
    assert removed == []  # the second copy still shows the file
    assert db.query(CommentAttachment).filter(CommentAttachment.s3_key == key).count() == 1
    ct._purge_comment(db, _copied(world, second["version_id"])["Cut the intro"].id, ct.PurgeCounts())
    assert key in deleted  # the last row takes the file


def test_storage_usage_counts_a_shared_file_once(world):
    db = world["db"]
    before = storage.project_storage_used_bytes(db, world["project"].id)
    duplicate_version(db, world["team"], world["source"].id, asset_name="Shorts")
    duplicate_version(db, world["team"], world["source"].id, asset_name="Story")
    assert storage.project_storage_used_bytes(db, world["project"].id) == before
    # With the source deleted, the copies still hold (and count) the file.
    world["source"].deleted_at = datetime.now(timezone.utc)
    db.flush()
    assert storage.project_storage_used_bytes(db, world["project"].id) == before


def test_orphan_sweep_treats_shared_files_as_live(world, monkeypatch):
    db = world["db"]
    duplicate_version(db, world["team"], world["source"].id, asset_name="Shorts")
    original = _media(db, world["source"].id)
    db.query(MediaFile).filter(MediaFile.id == original.id).delete(synchronize_session=False)
    db.flush()
    old = datetime.now(timezone.utc) - timedelta(hours=48)
    keys = [(original.s3_key_raw, old, 1000), (original.s3_key_processed + "/720p/seg001.ts", old, 10)]
    monkeypatch.setattr(settings, "orphan_sweep_grace_hours", 24)
    monkeypatch.setattr(settings, "orphan_sweep_delete", True)
    monkeypatch.setattr(ct, "list_keys", lambda prefix: [k for k in keys if k[0].startswith(prefix)])
    deleted = _deleted(monkeypatch)
    counts = ct._sweep_orphan_s3(db)
    assert counts.orphans == 0 and deleted == []


def test_shared_file_is_served_through_any_row_the_caller_may_use(world):
    db = world["db"]
    result = duplicate_version(db, world["team"], world["source"].id, asset_name="Shorts")
    thumb = _media(db, world["source"].id).s3_key_thumbnail
    # A share link that covers only the copy: the row of the source is refused, the copy's accepted.
    seen = []

    def only_copy(asset, version_id):
        seen.append(asset.id)
        if asset.id != result["asset_id"]:
            raise HTTPException(404, "Not in this share")

    asset, media, _, version_id = authorize_file(db, world["team"], thumb, share_context=True, accept=only_copy)
    assert asset.id == result["asset_id"] and version_id == result["version_id"]
    with pytest.raises(HTTPException) as exc:
        authorize_file(db, world["outsider"], thumb)
    assert exc.value.status_code == 404
