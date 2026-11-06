"""JR Content P1-B26: an upload that is not a video is refused at once, a failed read is retried.

Before: a text file named `.mp4` was accepted, sat at "Converting" and was
reported failed only ~13 minutes later. The conversion task retried *every*
error three times a minute apart, and the retries queued behind other
conversions in the single-slot queue. Nothing looked at the content when the
upload finished.

Covered here:
  * the content check itself, on real files made by the local ffmpeg
    (valid MP4/MOV/WebM/audio-only; text, empty, cut-off, still image),
  * the split between "the bytes are not media" (permanent) and "the read
    failed" (transient), including that a transient marker always wins,
  * the conversion task: permanent -> failed now, no retry; transient -> retry,
  * POST /upload/complete: unreadable -> 422 and the version discarded,
    playable or undecided -> queued as before, images not probed,
  * conversion progress recorded by the worker and returned by the version list.
"""
import subprocess
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from celery.exceptions import Retry
from fastapi import BackgroundTasks, HTTPException
from sqlalchemy.orm import Session

import apps.api.routers.assets as assets_module
import apps.api.routers.upload as upload_module
import apps.api.services.s3_service as s3_service
import apps.api.tasks.cleanup_tasks as cleanup
from apps.api.models.asset import Asset, AssetType, AssetVersion, FileType, MediaFile, ProcessingStatus
from apps.api.models.pending_transcode import PendingTranscode
from apps.api.models.project import Project, ProjectMember, ProjectRole, ProjectType
from apps.api.models.user import User
from apps.api.schemas.upload import CompleteUploadRequest
from apps.api.tasks import transcode_tasks
from packages.transcoder import source_check
from packages.transcoder.source_check import (
    PLAYABLE, UNKNOWN, UNREADABLE, SourceCheck, UnreadableSource, check_media_source, is_unreadable_source,
)

MB = 1024 * 1024


# ─── Real files from the local ffmpeg ─────────────────────────────────────────

def _ffmpeg(*args):
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args], check=True, timeout=60, capture_output=True)


@pytest.fixture(scope="module")
def media(tmp_path_factory):
    d = tmp_path_factory.mktemp("media")
    src = ["-f", "lavfi", "-i", "testsrc2=size=64x48:rate=10:duration=1"]
    tone = ["-f", "lavfi", "-i", "sine=frequency=440:duration=1"]
    _ffmpeg(*src, *tone, "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(d / "ok.mp4"))
    _ffmpeg(*src, "-c:v", "libx264", "-pix_fmt", "yuv420p", str(d / "ok.mov"))
    _ffmpeg(*src, "-c:v", "libvpx", "-b:v", "100k", str(d / "ok.webm"))
    _ffmpeg(*tone, "-c:a", "aac", str(d / "voice.mp4"))
    _ffmpeg("-f", "lavfi", "-i", "color=c=red:size=32x24", "-frames:v", "1", str(d / "picture.png"))
    (d / "text.mp4").write_text("this is not a video\n")
    (d / "empty.mp4").write_bytes(b"")
    data = (d / "ok.mp4").read_bytes()
    # moov follows mdat in ffmpeg's default MP4, so cutting the end removes the index:
    # the file a stopped transfer would leave behind.
    (d / "cut.mp4").write_bytes(data[: len(data) // 2])
    (d / "picture.mp4").write_bytes((d / "picture.png").read_bytes())
    return d


@pytest.mark.parametrize("name", ["ok.mp4", "ok.mov", "ok.webm", "voice.mp4"])
def test_real_videos_and_audio_only_files_are_playable(media, name):
    assert check_media_source(str(media / name)).verdict == PLAYABLE


@pytest.mark.parametrize("name", ["text.mp4", "empty.mp4", "cut.mp4", "picture.mp4", "picture.png"])
def test_files_that_are_not_recordings_are_unreadable(media, name):
    result = check_media_source(str(media / name))
    assert result.verdict == UNREADABLE, result.reason
    assert result.unreadable


# ─── What counts as permanent ─────────────────────────────────────────────────

@pytest.mark.parametrize("stderr", [
    "x.mp4: Invalid data found when processing input",
    "[mov,mp4] moov atom not found",
    "Decoder (codec xyz) not found for input stream #0:0",
    "Decoding requested, but no decoder found for: none",
    "Could not find codec parameters for stream 0 (Video: h264, none): unspecified size",
])
def test_statements_about_the_bytes_are_permanent(stderr):
    assert is_unreadable_source(stderr)


@pytest.mark.parametrize("stderr", [
    "http://s3/x: Server returned 403 Forbidden (access denied)",
    "Connection refused",
    "Connection reset by peer\nInvalid data found when processing input",
    "Connection timed out",
    "I/O error",
    "",
    None,
    "something nobody has seen before",
])
def test_a_failed_read_or_an_unknown_message_stays_retryable(stderr):
    assert not is_unreadable_source(stderr)


def _proc(returncode=0, stdout="", stderr=""):
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


def test_an_undecodable_codec_is_unreadable():
    probe = _proc(stdout='{"format": {"format_name": "matroska,webm"}, "streams": [{"codec_type": "video"}]}')
    decode = _proc(1, stderr="Decoder (codec none) not found for input stream #0:0")
    calls = iter([probe, decode])
    assert check_media_source("http://s3/x?X-Amz-Signature=s", run=lambda *a, **k: next(calls)).verdict == UNREADABLE


def test_storage_trouble_is_undecided_and_never_quotes_the_signed_address():
    url = "http://s3/raw/x.mp4?X-Amz-Credential=key&X-Amz-Signature=secret"
    result = check_media_source(url, run=lambda *a, **k: _proc(1, stderr=f"{url}: Server returned 403 Forbidden"))
    assert result.verdict == UNKNOWN
    assert "secret" not in result.reason and "key" not in result.reason


def test_running_out_of_time_is_undecided():
    def slow(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, kwargs["timeout"])
    assert check_media_source("http://s3/x", probe_timeout=1, decode_timeout=1, run=slow).verdict == UNKNOWN


def test_a_decode_that_yields_no_frame_without_a_reason_is_undecided():
    probe = _proc(stdout='{"format": {"format_name": "mov,mp4"}, "streams": [{"codec_type": "video"}]}')
    calls = iter([probe, _proc(0, stdout="#tb 0: 1/25\n")])
    assert check_media_source("http://s3/x", run=lambda *a, **k: next(calls)).verdict == UNKNOWN


def test_a_cover_picture_alone_is_not_a_video_track():
    probe = _proc(stdout='{"format": {"format_name": "mov,mp4"}, "streams": '
                         '[{"codec_type": "video", "disposition": {"attached_pic": 1}}]}')
    assert check_media_source("http://s3/x", run=lambda *a, **k: probe).verdict == UNREADABLE


# ─── The transcoder marks the result ──────────────────────────────────────────

@pytest.mark.parametrize("error, unreadable", [
    ("ffprobe exited 1: x: Invalid data found when processing input", True),
    ("ffprobe exited 1: x: Connection refused", False),
])
def test_the_transcoder_says_whether_its_failure_is_about_the_bytes(error, unreadable):
    import asyncio
    from packages.transcoder.base import TranscodeJob
    from packages.transcoder.ffmpeg_transcoder import FFmpegTranscoder

    transcoder = FFmpegTranscoder(MagicMock(), "bucket", "http://s3")
    job = TranscodeJob(media_id="m", version_id="v", input_s3_key="raw/x", output_s3_prefix="out")
    with patch.object(FFmpegTranscoder, "_run", side_effect=RuntimeError(error)):
        result = asyncio.run(transcoder.transcode(job))
    assert result.success is False
    assert result.unreadable_source is unreadable


# ─── The conversion task ──────────────────────────────────────────────────────

class _FakeRedis:
    def __init__(self):
        self.values, self.hashes = {}, {}

    def set(self, key, value, ex=None):
        self.values[key] = str(value)

    def delete(self, key):
        self.values.pop(key, None)

    def mget(self, keys):
        return [self.values.get(k) for k in keys]

    def hset(self, name, key, value):
        self.hashes.setdefault(name, {})[key] = value


def _drive(failure, retries=0):
    """Run the real process_asset with `failure` raised by the video pipeline."""
    asset = SimpleNamespace(id=uuid.uuid4(), project_id=uuid.uuid4(), asset_type=AssetType.video)
    version = SimpleNamespace(id=uuid.uuid4(), processing_status=ProcessingStatus.uploading)
    media_file = SimpleNamespace(version_id=version.id, s3_key_raw="raw/x")
    db = MagicMock()
    db.query.return_value = db
    db.filter.return_value = db
    db.first.side_effect = [version, asset, media_file, version, asset]
    published, redis = [], _FakeRedis()
    task = transcode_tasks.process_asset
    task = getattr(task, "_get_current_object", lambda: task)()
    task.push_request(retries=retries, called_directly=False)
    try:
        with patch.object(transcode_tasks, "SessionLocal", return_value=db), \
             patch.object(transcode_tasks, "get_s3_client", return_value=MagicMock()), \
             patch.object(transcode_tasks, "_process_video", side_effect=failure), \
             patch.object(transcode_tasks, "_failure_store", return_value=redis), \
             patch.object(transcode_tasks, "_publish_event", side_effect=lambda p, t, pl: published.append(t)), \
             patch.object(task, "retry", side_effect=Retry()) as retry:
            try:
                task.run(str(asset.id), str(version.id))
                outcome = "returned"
            except Retry:
                outcome = "retry_scheduled"
            except Exception:
                outcome = "raised"
            retries_asked = retry.call_count
    finally:
        task.pop_request()
    return SimpleNamespace(version=version, published=published, outcome=outcome, retries=retries_asked)


@pytest.mark.parametrize("failure", [
    UnreadableSource("Transcode failed: x: Invalid data found when processing input"),
    subprocess.CalledProcessError(1, ["ffmpeg"], stderr=b"in.img: Invalid data found when processing input"),
])
def test_unusable_media_fails_on_the_first_attempt_without_a_retry(failure):
    r = _drive(failure)
    assert r.outcome == "returned"
    assert r.retries == 0
    assert r.version.processing_status == ProcessingStatus.failed
    assert r.published == ["transcode_failed"]


@pytest.mark.parametrize("failure", [
    RuntimeError("Transcode failed: ffprobe exited 1: x: Connection refused"),
    subprocess.CalledProcessError(1, ["ffmpeg"], stderr=b"Server returned 503 Service Unavailable"),
    RuntimeError("anything else"),
])
def test_a_failed_read_is_retried_and_stays_processing(failure):
    r = _drive(failure)
    assert r.outcome == "retry_scheduled"
    assert r.retries == 1
    assert r.version.processing_status == ProcessingStatus.processing
    assert r.published == []


def test_a_failed_read_still_fails_when_the_retries_are_spent():
    r = _drive(RuntimeError("Connection refused"), retries=3)
    assert r.outcome == "raised"
    assert r.version.processing_status == ProcessingStatus.failed


def test_progress_is_recorded_with_an_expiry_and_cleared():
    redis = _FakeRedis()
    with patch.object(transcode_tasks, "_failure_store", return_value=redis):
        assert transcode_tasks._record_progress("v1", 42) is True
        assert redis.values["transcode:progress:v1"] == "42"
        transcode_tasks._clear_progress("v1")
    assert "transcode:progress:v1" not in redis.values


def test_progress_that_cannot_be_written_never_fails_the_conversion():
    broken = MagicMock()
    broken.set.side_effect = ConnectionError("redis down")
    broken.delete.side_effect = ConnectionError("redis down")
    with patch.object(transcode_tasks, "_failure_store", return_value=broken):
        assert transcode_tasks._record_progress("v1", 5) is False
        transcode_tasks._clear_progress("v1")


# ─── POST /upload/complete against real Postgres ──────────────────────────────

@pytest.fixture
def db():
    """Every session transaction is a SAVEPOINT, so what was committed is observable."""
    from apps.api.database import engine
    conn = engine.connect()
    trans = conn.begin()
    session = Session(bind=conn, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()
        trans.rollback()
        conn.close()


@pytest.fixture
def storage(monkeypatch):
    calls = {"deleted": [], "probed": []}
    monkeypatch.setattr(upload_module, "list_upload_parts", lambda k, u: [{"PartNumber": 1, "ETag": '"e"', "Size": 23 * MB}])
    monkeypatch.setattr(upload_module, "complete_multipart_upload", lambda k, u, p: None)
    monkeypatch.setattr(upload_module, "head_object_size", lambda k: 23 * MB)
    monkeypatch.setattr(cleanup, "abort_multipart_upload", lambda k, u: None)
    monkeypatch.setattr(cleanup, "delete_object", lambda k: calls["deleted"].append(k))
    monkeypatch.setattr(cleanup, "delete_prefix", lambda k: calls["deleted"].append(k))
    monkeypatch.setattr(s3_service, "generate_internal_presigned_get_url", lambda key, expires_in=0: f"http://s3/{key}?sig")
    return calls


def _verdict(monkeypatch, storage, verdict):
    def check(url, **kwargs):
        storage["probed"].append((url, kwargs))
        return SourceCheck(verdict, "test")
    monkeypatch.setattr(source_check, "check_media_source", check)


def _seed(db, mime="video/mp4", earlier_ready=False):
    owner = User(email=f"p1b26-{uuid.uuid4()}@t.local", name="t")
    db.add(owner)
    db.flush()
    project = Project(name="t", project_type=ProjectType.personal, created_by=owner.id)
    db.add(project)
    db.flush()
    db.add(ProjectMember(project_id=project.id, user_id=owner.id, role=ProjectRole.editor))
    asset = Asset(project_id=project.id, name="t", asset_type=AssetType.video, created_by=owner.id)
    db.add(asset)
    db.flush()
    statuses = ([ProcessingStatus.ready] if earlier_ready else []) + [ProcessingStatus.uploading]
    versions = []
    for number, status in enumerate(statuses, start=1):
        version = AssetVersion(asset_id=asset.id, version_number=number, processing_status=status,
                               created_by=owner.id, upload_id=f"u-{number}")
        db.add(version)
        db.flush()
        db.add(MediaFile(version_id=version.id, file_type=FileType.video, original_filename="clip.mp4",
                         mime_type=mime, file_size_bytes=23 * MB, s3_key_raw=f"raw/p/a/{version.id}/original.mp4"))
        versions.append(version)
    db.commit()
    return owner, asset, versions[-1]


def _complete(db, owner, asset, version):
    body = CompleteUploadRequest(s3_key=f"raw/p/a/{version.id}/original.mp4", upload_id=version.upload_id,
                                 asset_id=asset.id, version_id=version.id)
    return upload_module.complete_upload(body, BackgroundTasks(), db=db, current_user=owner)


def _committed(db, model, row_id):
    db.rollback()
    db.expire_all()
    return db.get(model, row_id)


def test_an_unplayable_upload_is_refused_and_discarded(db, storage, monkeypatch):
    owner, asset, version = _seed(db)
    _verdict(monkeypatch, storage, UNREADABLE)

    with pytest.raises(HTTPException) as refused:
        _complete(db, owner, asset, version)

    assert refused.value.status_code == 422
    assert "not a video that can be played" in refused.value.detail
    assert storage["probed"][0][0] == f"http://s3/raw/p/a/{version.id}/original.mp4?sig"
    # Short limits inside the request: a slow read is undecided, not refused.
    assert storage["probed"][0][1]["probe_timeout"] <= 5 and storage["probed"][0][1]["decode_timeout"] <= 8
    stored = _committed(db, AssetVersion, version.id)
    assert stored.deleted_at is not None and stored.processing_status == ProcessingStatus.uploading
    assert _committed(db, Asset, asset.id).deleted_at is not None, "an asset with no version left goes too"
    assert f"raw/p/a/{version.id}/original.mp4" in storage["deleted"]
    assert db.query(PendingTranscode).filter(PendingTranscode.version_id == version.id).count() == 0


def test_a_refused_new_version_keeps_the_asset_and_its_earlier_versions(db, storage, monkeypatch):
    owner, asset, version = _seed(db, earlier_ready=True)
    _verdict(monkeypatch, storage, UNREADABLE)

    with pytest.raises(HTTPException):
        _complete(db, owner, asset, version)

    assert _committed(db, AssetVersion, version.id).deleted_at is not None
    assert _committed(db, Asset, asset.id).deleted_at is None


@pytest.mark.parametrize("verdict", [PLAYABLE, UNKNOWN])
def test_a_playable_or_undecided_upload_is_queued_as_before(db, storage, monkeypatch, verdict):
    owner, asset, version = _seed(db)
    _verdict(monkeypatch, storage, verdict)

    answer = _complete(db, owner, asset, version)

    assert answer.status == "processing"
    assert _committed(db, AssetVersion, version.id).processing_status == ProcessingStatus.processing
    assert db.query(PendingTranscode).filter(PendingTranscode.version_id == version.id).count() == 1


def test_an_image_upload_is_not_probed_as_video(db, storage, monkeypatch):
    owner, asset, version = _seed(db, mime="image/png")
    _verdict(monkeypatch, storage, UNREADABLE)

    assert _complete(db, owner, asset, version).status == "processing"
    assert storage["probed"] == []


def test_a_concurrent_completion_that_claimed_the_version_is_not_discarded(db, storage, monkeypatch):
    """The probe takes time; if another request queued the version meanwhile, it stays."""
    owner, asset, version = _seed(db)

    def claimed_meanwhile(url, **kwargs):
        from sqlalchemy import text
        db.connection().execute(text("UPDATE asset_versions SET processing_status = 'processing' WHERE id = :id"),
                                {"id": version.id})
        return SourceCheck(UNREADABLE, "test")
    monkeypatch.setattr(source_check, "check_media_source", claimed_meanwhile)

    answer = _complete(db, owner, asset, version)

    assert answer.status == "processing"
    assert storage["deleted"] == []


# ─── The version list carries the conversion percent ──────────────────────────

def test_the_version_list_reports_the_percent_of_a_running_conversion(db, monkeypatch):
    owner, asset, version = _seed(db, earlier_ready=True)
    version.processing_status = ProcessingStatus.processing
    db.commit()
    redis = _FakeRedis()
    redis.set(f"transcode:progress:{version.id}", 42)
    monkeypatch.setattr("apps.api.services.event_service._get_sync_redis", lambda: redis)

    listed = assets_module.list_asset_versions(asset.id, db=db, current_user=owner)

    by_id = {v.id: v for v in listed}
    assert by_id[version.id].processing_percent == 42
    assert all(v.processing_percent is None for v in listed if v.id != version.id)


def test_the_version_list_loads_without_progress_when_redis_is_down(db, monkeypatch):
    owner, asset, version = _seed(db)
    version.processing_status = ProcessingStatus.processing
    db.commit()
    broken = MagicMock()
    broken.mget.side_effect = ConnectionError("redis down")
    monkeypatch.setattr("apps.api.services.event_service._get_sync_redis", lambda: broken)

    listed = assets_module.list_asset_versions(asset.id, db=db, current_user=owner)

    assert [v.processing_percent for v in listed] == [None]


def test_recorded_percents_are_clamped_and_junk_is_ignored(monkeypatch):
    redis = _FakeRedis()
    a, b, c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    redis.values.update({f"transcode:progress:{a}": "140", f"transcode:progress:{b}": "x"})
    monkeypatch.setattr("apps.api.services.event_service._get_sync_redis", lambda: redis)
    assert assets_module._conversion_percents([a, b, c]) == {a: 100}
