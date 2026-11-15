"""P2-B10: per-version private SRT, validation, role boundaries and object lifecycle."""
# ruff: noqa: F401, F811

import uuid

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, override_settings

from apps.jrc_video.models import VideoVersion
from apps.jrc_video.tests.test_player import two_versions
from apps.jrc_video.tests.test_upload import _channels_on, ff, make_user, setup, url, workspace
from apps.members.models import CustomRole, WorkspaceMembership

pytestmark = pytest.mark.django_db
SRT = '1\n00:00:00,000 --> 00:00:01,500\nŽlutý klip <script>alert("x")</script>\n\n2\n00:00:01,500 --> 00:00:03,000\nDruhý řádek\n'.encode()


@pytest.fixture(autouse=True)
def memory_storage(settings):
    settings.STORAGES = {**settings.STORAGES, "default": {"BACKEND": "django.core.files.storage.InMemoryStorage"}}


@pytest.fixture
def versions(client, ff, workspace, user, setup):
    return two_versions(client, workspace, setup, user)


def address(workspace, post, version):
    return url("transcript", workspace, post, ("version_id", version.pk))


def upload(client, path, data=SRT, name="Přepis.srt"):
    return client.post(path, {"file": SimpleUploadedFile(name, data, content_type="application/octet-stream")})


@pytest.mark.parametrize(
    "role,allowed",
    [
        ("owner", True),
        ("manager", True),
        ("editor", True),
        ("contributor", False),
        ("client", False),
        ("viewer", False),
    ],
)
def test_roles_read_and_mutate(client, workspace, user, setup, versions, role, allowed):
    path = address(workspace, setup, versions[0])
    assert upload(client, path).status_code == 200
    person = make_user(workspace, f"srt-{role}@example.invalid", role)
    client.force_login(person)
    assert client.get(path).content == SRT
    assert client.get(path + "?preview=1").json()["text"] == SRT.decode()
    assert upload(client, path).status_code == (200 if allowed else 403)
    assert client.post(path, {"delete": "1"}).status_code == (200 if allowed else 403)


def test_contributor_may_upload_own_content_and_custom_role_may_not(client, workspace, setup, versions):
    person = make_user(workspace, "srt-author@example.invalid", "contributor")
    setup.author = person
    setup.save(update_fields=["author"])
    client.force_login(person)
    path = address(workspace, setup, versions[0])
    assert upload(client, path).status_code == 200
    role = CustomRole.objects.create(
        organization=workspace.organization,
        name="Custom SRT",
        permissions={"create_posts": True, "edit_others_posts": True},
    )
    WorkspaceMembership.objects.filter(user=person).update(custom_role=role)
    assert upload(client, path).status_code == 403
    assert client.get(path).status_code == 200


@pytest.mark.parametrize(
    "data,name,status",
    [
        (b"", "x.srt", 400),
        (SRT, "x.txt", 400),
        (b"not subtitles", "x.srt", 400),
        (SRT.decode().encode("cp1250"), "x.srt", 400),
        (SRT.replace(b"00:00:01,500", b"00:60:01,500"), "x.srt", 400),
        (b"1\n00:00:01,000 --> 00:00:00,500\nText", "x.srt", 400),
        (b"1\n00:00:00,000 --> 00:00:01,000\n", "x.srt", 400),
        (SRT + b"\0", "x.srt", 400),
        (b"x" * (1024 * 1024 + 1), "x.srt", 413),
        (b"9" * 5000 + b"\n00:00:00,000 --> 00:00:01,000\nText", "x.srt", 400),
        (b"1\n" + b"9" * 5000 + b":00:00,000 --> 00:00:01,000\nText", "x.srt", 400),
    ],
    ids=[
        "empty",
        "extension",
        "text",
        "encoding",
        "minutes",
        "reversed",
        "missing-text",
        "control",
        "oversize",
        "long-number",
        "long-hours",
    ],
)
def test_invalid_srt_keeps_existing_file(client, workspace, setup, versions, data, name, status):
    path = address(workspace, setup, versions[0])
    assert upload(client, path).status_code == 200
    assert upload(client, path, data, name).status_code == status
    assert client.get(path).content == SRT


@pytest.mark.parametrize(
    "data",
    [SRT, b"\xef\xbb\xbf" + SRT.replace(b"\n", b"\r\n"), SRT.replace(b"\n\n", b"\n\n\n\n")],
    ids=["utf8", "bom-crlf", "extra-blank-lines"],
)
def test_round_trip_preview_and_private_response(client, workspace, setup, versions, data):
    path = address(workspace, setup, versions[0])
    assert upload(client, path, data).status_code == 200
    response = client.get(path)
    assert response.content == data
    assert response["X-Content-Type-Options"] == "nosniff"
    assert "no-store" in response["Cache-Control"]
    assert response["Content-Disposition"].startswith("attachment;")
    assert "%C5%99" in response["Content-Disposition"]
    assert client.get(path + "?preview=1").json()["text"] == data.decode("utf-8-sig")
    rows = client.get(url("upload_status", workspace, setup)).json()["channels"][0]["versions"]
    assert rows[1]["transcript_filename"] == "Přepis.srt" and rows[0]["transcript_filename"] == ""
    assert "jrc_video_transcripts/" not in str(rows)
    assert client.get(address(workspace, setup, versions[1])).status_code == 404


def test_replace_delete_cascade_and_rollback(
    client, workspace, setup, versions, django_capture_on_commit_callbacks, monkeypatch
):
    path = address(workspace, setup, versions[0])
    assert upload(client, path).status_code == 200
    version = VideoVersion.objects.get(pk=versions[0].pk)
    storage, old = version.transcript.storage, version.transcript.name
    with django_capture_on_commit_callbacks(execute=True):
        assert upload(client, path, SRT.replace(b"klip", b"film")).status_code == 200
    assert not storage.exists(old)
    version.refresh_from_db()
    replacement = version.transcript.name
    original_save = VideoVersion.save

    def failed_save(*args, **kwargs):
        raise RuntimeError("local write failed")

    monkeypatch.setattr(VideoVersion, "save", failed_save)
    with pytest.raises(RuntimeError), django_capture_on_commit_callbacks(execute=True):
        upload(client, path)
    assert storage.listdir("jrc_video_transcripts/" + str(version.pk))[1] == [replacement.rsplit("/", 1)[-1]]
    monkeypatch.setattr(VideoVersion, "save", original_save)
    with django_capture_on_commit_callbacks(execute=True):
        assert client.post(path, {"delete": "1"}).status_code == 200
    assert not storage.exists(replacement)
    assert client.get(path).status_code == 404
    assert upload(client, path).status_code == 200
    version.refresh_from_db()
    key = version.transcript.name
    with django_capture_on_commit_callbacks(execute=True):
        version.video.platform_post.post.delete()
    assert not storage.exists(key)


def test_session_csrf_scope_archived_and_revoked_membership(client, workspace, user, setup, versions):
    path = address(workspace, setup, versions[0])
    assert upload(client, path).status_code == 200
    secure = Client(enforce_csrf_checks=True)
    secure.force_login(user)
    assert upload(secure, path).status_code == 403
    assert secure.post(path, {"delete": "1"}).status_code == 403
    assert client.get(path.replace(str(setup.pk), str(uuid.uuid4()))).status_code == 404
    workspace.is_archived = True
    workspace.save(update_fields=["is_archived"])
    assert upload(client, path).status_code == 403
    workspace.is_archived = False
    workspace.save(update_fields=["is_archived"])
    WorkspaceMembership.objects.filter(user=user, workspace=workspace).delete()
    # P2-FABLE: a revoked member is a stranger and gets 404 on every jrc_* URL.
    assert client.get(path).status_code == 404
    assert client.post(path, {"delete": "1"}).status_code == 404
    assert upload(client, path).status_code == 404
    client.logout()
    assert client.get(path).status_code == 302
