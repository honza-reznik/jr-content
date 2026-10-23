import uuid
from unittest.mock import Mock, patch

import pytest
import requests
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.jrc_video.client import FreeFrameClient, FreeFrameError
from apps.jrc_video.models import VideoAccount, VideoProject, VideoSyncRequest
from apps.jrc_video.services import sync_workspace
from apps.members.models import WorkspaceMembership
from apps.workspaces.models import Workspace

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def empty_token_cache():
    from apps.jrc_video.client import _TOKENS

    _TOKENS.clear()
    yield
    _TOKENS.clear()


@pytest.fixture
def workspace(organization):
    return Workspace.objects.create(organization=organization, name="Video client")


@pytest.fixture
def api():
    service_id, project_id = str(uuid.uuid4()), str(uuid.uuid4())
    accounts = {}
    members = {service_id: "owner"}

    def request(method, path, payload=None):
        if path.startswith("/jrc-sync/projects/"):
            return {"id": project_id, "service_user_id": service_id}
        if path.startswith("/jrc-sync/accounts/"):
            return {"id": accounts.setdefault(path, str(uuid.uuid4()))}
        if method == "GET":
            return [{"user_id": key, "role": value} for key, value in members.items()]
        if method == "POST":
            members[payload["user_id"]] = payload["role"]
        if method == "PATCH":
            members[path.rsplit("/", 1)[-1]] = payload["role"]
        if method == "DELETE":
            members.pop(path.rsplit("/", 1)[-1])

    client = Mock()
    client.request.side_effect = request
    client.members = members
    client.service_id = service_id
    return client


@pytest.mark.parametrize(
    "role, expected",
    [
        ("owner", "editor"),
        ("manager", "editor"),
        ("editor", "editor"),
        ("contributor", "editor"),
        ("client", "reviewer"),
        ("viewer", "viewer"),
    ],
)
def test_role_mapping_and_repeated_sync(workspace, user, api, role, expected):
    WorkspaceMembership.objects.create(workspace=workspace, user=user, workspace_role=role)
    first = sync_workspace(workspace.pk, api=api)
    account = VideoAccount.objects.get(user=user)
    assert api.members[str(account.ff_user_id)] == expected
    api.request.reset_mock()
    second = sync_workspace(workspace.pk, api=api)
    assert first.ff_project_id == second.ff_project_id
    assert VideoAccount.objects.count() == VideoProject.objects.count() == 1
    assert not any(call.args[0] in {"POST", "PATCH", "DELETE"} for call in api.request.call_args_list)


def test_role_change_removal_and_stale_member_repair(workspace, user, api):
    member = WorkspaceMembership.objects.create(workspace=workspace, user=user, workspace_role="editor")
    sync_workspace(workspace.pk, api=api)
    member.workspace_role = "client"
    member.save()
    sync_workspace(workspace.pk, api=api)
    account = VideoAccount.objects.get(user=user)
    assert api.members[str(account.ff_user_id)] == "reviewer"
    member.delete()
    api.members[str(uuid.uuid4())] = "editor"
    sync_workspace(workspace.pk, api=api)
    assert api.members == {api.service_id: "owner"}


@pytest.mark.parametrize("blocked", ["user", "workspace"])
def test_blocked_user_or_archived_workspace_has_no_access(workspace, user, api, blocked):
    WorkspaceMembership.objects.create(workspace=workspace, user=user, workspace_role="owner")
    sync_workspace(workspace.pk, api=api)
    if blocked == "user":
        user.is_active = False
        user.save()
    else:
        workspace.is_archived = True
        workspace.save()
    sync_workspace(workspace.pk, api=api)
    assert api.members == {api.service_id: "owner"}


def test_custom_role_is_conservative(workspace, user, organization, api):
    from apps.members.models import CustomRole

    custom = CustomRole.objects.create(organization=organization, name="Custom", permissions={"upload_media": True})
    WorkspaceMembership.objects.create(workspace=workspace, user=user, workspace_role="owner", custom_role=custom)
    sync_workspace(workspace.pk, api=api)
    account = VideoAccount.objects.get(user=user)
    assert api.members[str(account.ff_user_id)] == "viewer"


def test_workspace_deletion_retains_remote_data_and_revokes_members(
    workspace, user, api, settings, django_capture_on_commit_callbacks
):
    WorkspaceMembership.objects.create(workspace=workspace, user=user, workspace_role="client")
    sync_workspace(workspace.pk, api=api)
    settings.JRC_FF_SYNC_ENABLED = True
    with (
        patch("apps.jrc_video.signals.FreeFrameClient", return_value=api),
        django_capture_on_commit_callbacks(execute=True) as callbacks,
    ):
        workspace.delete()
    assert api.members == {api.service_id: "owner"}
    assert not VideoSyncRequest.objects.exists()
    assert not callbacks
    assert not any(call.args[0] == "DELETE" and "/members/" not in call.args[1] for call in api.request.call_args_list)


def test_workspace_rename_does_not_use_cached_missing_relation(
    workspace, api, settings, django_capture_on_commit_callbacks
):
    assert not hasattr(workspace, "videoproject")
    sync_workspace(workspace.pk, api=api)
    settings.JRC_FF_SYNC_ENABLED = True
    with (
        patch("apps.jrc_video.services.FreeFrameClient", return_value=api),
        django_capture_on_commit_callbacks(execute=True),
    ):
        workspace.name = "Renamed"
        workspace.save(update_fields=["name"])
    assert any(
        call.args == ("PUT", f"/jrc-sync/projects/{workspace.pk}", {"name": "Renamed"})
        for call in api.request.call_args_list
    )


def test_signal_runs_after_commit_and_retains_failed_retry(
    workspace, user, settings, django_capture_on_commit_callbacks
):
    settings.JRC_FF_SYNC_ENABLED = True
    with (
        patch("apps.jrc_video.services.sync_workspace", side_effect=FreeFrameError("Unavailable")),
        django_capture_on_commit_callbacks(execute=True) as callbacks,
    ):
        WorkspaceMembership.objects.create(workspace=workspace, user=user, workspace_role="client")
        assert len(callbacks) == 0
    assert len(callbacks) == 1
    assert VideoSyncRequest.objects.get(workspace=workspace).last_error == "Unavailable"


def test_signal_rollback_does_not_call_api(workspace, user, settings):
    from django.db import transaction

    settings.JRC_FF_SYNC_ENABLED = True
    with patch("apps.jrc_video.services.sync_workspace") as sync, pytest.raises(ValueError), transaction.atomic():
        WorkspaceMembership.objects.create(workspace=workspace, user=user)
        raise ValueError("rollback")
    sync.assert_not_called()
    assert not VideoSyncRequest.objects.exists()


def test_moving_membership_also_revokes_previous_workspace(
    workspace, user, organization, settings, django_capture_on_commit_callbacks
):
    other = Workspace.objects.create(organization=organization, name="Other client")
    member = WorkspaceMembership.objects.create(workspace=workspace, user=user, workspace_role="client")
    settings.JRC_FF_SYNC_ENABLED = True
    with (
        patch("apps.jrc_video.services.sync_workspace") as sync,
        django_capture_on_commit_callbacks(execute=True),
    ):
        member.workspace = other
        member.save(update_fields=["workspace"])
    assert {call.args[0] for call in sync.call_args_list} == {workspace.pk, other.pk}


def test_user_deletion_aborts_if_remote_revocation_fails(workspace, user, api, settings):
    from django.db import transaction

    from apps.accounts.models import User

    WorkspaceMembership.objects.create(workspace=workspace, user=user, workspace_role="client")
    sync_workspace(workspace.pk, api=api)
    settings.JRC_FF_SYNC_ENABLED = True
    failing = Mock()
    failing.request.side_effect = FreeFrameError("Unavailable")
    with (
        patch("apps.jrc_video.signals.FreeFrameClient", return_value=failing),
        pytest.raises(FreeFrameError),
        transaction.atomic(),
    ):
        user.delete()
    assert User.objects.filter(pk=user.pk).exists()


def test_sync_keeps_change_queued_during_api_request(workspace, api):
    from django.utils import timezone

    VideoSyncRequest.objects.create(workspace=workspace)
    original = api.request.side_effect

    def changed_during_request(method, path, payload=None):
        VideoSyncRequest.objects.filter(workspace=workspace).update(requested_at=timezone.now())
        return original(method, path, payload)

    api.request.side_effect = changed_during_request
    sync_workspace(workspace.pk, api=api)
    assert VideoSyncRequest.objects.filter(workspace=workspace).exists()


def test_command_reports_errors_and_repairs_pending(workspace, api):
    with patch("apps.jrc_video.management.commands.jrc_sync_freeframe.FreeFrameClient", return_value=api):
        api.request.side_effect = FreeFrameError("Unavailable")
        with pytest.raises(CommandError):
            call_command("jrc_sync_freeframe")
    assert VideoSyncRequest.objects.get(workspace=workspace).last_error == "Unavailable"
    with patch("apps.jrc_video.management.commands.jrc_sync_freeframe.FreeFrameClient", return_value=api):
        # Replace with the independent healthy fake.
        api.request.side_effect = lambda method, path, payload=None: (
            {"id": str(uuid.uuid4()), "service_user_id": api.service_id} if method == "PUT" else []
        )
        call_command("jrc_sync_freeframe", pending=True)
    assert not VideoSyncRequest.objects.exists()


@pytest.mark.parametrize("failure", [requests.Timeout(), requests.ConnectionError("secret")])
def test_client_network_errors_are_safe(failure):
    with (
        patch("apps.jrc_video.client.requests.request", side_effect=failure),
        pytest.raises(FreeFrameError, match="unavailable") as exc,
    ):
        FreeFrameClient().request("GET", "/projects")
    assert "secret" not in str(exc.value)


def test_client_does_not_follow_redirect_or_disclose_response():
    response = Mock(status_code=302, text="secret", headers={"Location": "http://example.invalid"})
    with (
        patch("apps.jrc_video.client.requests.request", return_value=response) as send,
        pytest.raises(FreeFrameError, match="HTTP 302") as exc,
    ):
        FreeFrameClient().request("GET", "/projects")
    assert "secret" not in str(exc.value)
    assert send.call_args.kwargs["allow_redirects"] is False


def test_client_login_token_is_only_in_memory():
    login = Mock(status_code=200)
    login.json.return_value = {"access_token": "memory-token"}
    result = Mock(status_code=204)
    with patch("apps.jrc_video.client.requests.request", side_effect=[login, result]) as send:
        assert FreeFrameClient().request("DELETE", "/projects/uuid/members/uuid") is None
    assert send.call_args.kwargs["headers"] == {"Authorization": "Bearer memory-token"}


def test_clients_share_login_in_process_and_refresh_expired_token():
    login = Mock(status_code=200)
    login.json.return_value = {"access_token": "first", "refresh_token": "refresh-first"}
    refreshed = Mock(status_code=200)
    refreshed.json.return_value = {"access_token": "second", "refresh_token": "refresh-second"}
    done = Mock(status_code=204)
    expired = Mock(status_code=401)
    with patch("apps.jrc_video.client.requests.request", side_effect=[login, done, expired, refreshed, done]) as send:
        FreeFrameClient().request("GET", "/projects")
        FreeFrameClient().request("GET", "/projects")
    paths = [call.args[1] for call in send.call_args_list]
    assert sum(path.endswith("/auth/login") for path in paths) == 1
    assert sum(path.endswith("/auth/refresh") for path in paths) == 1
    assert send.call_args.kwargs["headers"] == {"Authorization": "Bearer second"}


def test_invalid_login_response_is_safe():
    response = Mock(status_code=200)
    response.json.return_value = {"unexpected": "secret"}
    with (
        patch("apps.jrc_video.client.requests.request", return_value=response),
        pytest.raises(FreeFrameError, match="invalid login response") as exc,
    ):
        FreeFrameClient().request("GET", "/projects")
    assert "secret" not in str(exc.value)


@pytest.mark.parametrize("result", [{}, {"id": "not-a-uuid"}, []])
def test_invalid_sync_response_is_safe(result):
    login = Mock(status_code=200)
    login.json.return_value = {"access_token": "first"}
    invalid = Mock(status_code=200)
    invalid.json.return_value = result
    with (
        patch("apps.jrc_video.client.requests.request", side_effect=[login, invalid]),
        pytest.raises(FreeFrameError, match="invalid synchronization response"),
    ):
        FreeFrameClient().request("PUT", f"/jrc-sync/projects/{uuid.uuid4()}", {"name": "Test"})


def test_blocking_user_without_memberships_survives_api_failure(
    user, settings, caplog, django_capture_on_commit_callbacks
):
    from django.utils import timezone

    WorkspaceMembership.objects.filter(user=user).delete()
    VideoAccount.objects.create(user=user, ff_user_id=uuid.uuid4(), synced_at=timezone.now())
    settings.JRC_FF_SYNC_ENABLED = True
    failing = Mock()
    failing.request.side_effect = FreeFrameError("Unavailable")
    with (
        patch("apps.jrc_video.services.FreeFrameClient", return_value=failing),
        django_capture_on_commit_callbacks(execute=True) as callbacks,
    ):
        user.is_active = False
        user.save(update_fields=["is_active"])
    assert len(callbacks) == 1
    failing.request.assert_called_once()
    assert "run jrc_sync_freeframe" in caplog.text
    user.refresh_from_db()
    assert user.is_active is False
