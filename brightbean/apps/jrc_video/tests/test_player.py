"""F5-B08: Video tab with the embedded player, version choice and "Final for publishing".

FreeFrame is the same recording fake as in the upload tests. The live check with a real video
and Chromium is ``scripts/F5-B08/check-video-tab.cjs``.
"""
# ruff: noqa: F401, F811  (fixtures are imported from test_upload.py and used by name)

import uuid

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.jrc_video import player, uploads
from apps.jrc_video.models import VideoAccount, VideoAsset, VideoVersion
from apps.jrc_video.tests.test_upload import (  # noqa: F401 - fixtures and helpers shared with the upload tests
    _channels_on,
    channel_key,
    ff,
    make_user,
    post_json,
    setup,
    start,
    started,
    url,
    workspace,
)
from apps.members.models import CustomRole, WorkspaceMembership

pytestmark = pytest.mark.django_db


def tab(client, workspace, post):
    return client.get(
        reverse("jrc_production:content_detail", kwargs={"workspace_id": workspace.id, "post_id": post.id})
        + "?tab=video"
    )


def two_versions(client, workspace, post, user):
    """v1 and v2 of the first channel, both converted."""
    client.force_login(user)
    started(client, workspace, post)
    VideoVersion.objects.update(status="ready")
    started(client, workspace, post)
    VideoVersion.objects.update(status="ready")
    return list(VideoVersion.objects.order_by("number"))


def final(client, workspace, post, version, **data):
    return post_json(client, url("video_final", workspace, post, ("version_id", version.pk)), data)


# --- the tab ---------------------------------------------------------------------------


def test_tab_embeds_the_player_through_the_ticket_form_not_a_link(client, ff, workspace, user, setup):
    versions = two_versions(client, workspace, setup, user)
    html = tab(client, workspace, setup).content.decode()
    asset = versions[0].video
    assert 'action="/video-sso/"' in html and "<iframe" in html and 'name="jrc-video-frame-1"' in html
    assert "jrc_video/video-tab.js" in html and "Final for publishing" in html
    # The viewer address and the FreeFrame version ids reach the page (not secrets), tokens never.
    assert f"/assets/{asset.ff_asset_id}" in html and str(versions[1].ff_version_id) in html
    assert "access_token" not in html and "Bearer" not in html and "ticket=" not in html


def test_tab_says_so_when_video_is_not_synchronized(client, ff, workspace, user, setup):
    VideoAccount.objects.filter(user=user).delete()
    client.force_login(user)
    html = tab(client, workspace, setup).content.decode()
    assert "The video player is not available yet" in html


def test_cards_give_the_player_address_per_channel_and_the_final_mark(client, ff, workspace, user, setup):
    versions = two_versions(client, workspace, setup, user)
    assert final(client, workspace, setup, versions[0]).status_code == 200
    card = client.get(url("upload_status", workspace, setup)).json()["channels"][0]
    assert (
        card["player_path"].startswith("/projects/")
        and f"/assets/{versions[0].video.ff_asset_id}" in card["player_path"]
    )
    assert card["final"]["number"] == 1 and card["final"]["by"] == user.display_name
    flags = {row["number"]: row["is_final"] for row in card["versions"]}
    assert flags == {1: True, 2: False}
    assert {row["number"]: row["ff_version_id"] for row in card["versions"]}[2] == str(versions[1].ff_version_id)


def test_channel_without_video_has_no_player_address(client, ff, workspace, user, setup):
    client.force_login(user)
    card = client.get(url("upload_status", workspace, setup)).json()["channels"][0]
    assert card["player_path"] == "" and card["final"] is None


# --- Final for publishing --------------------------------------------------------------


def test_manager_marks_one_version_final_and_the_latest_click_wins(client, ff, workspace, user, setup):
    versions = two_versions(client, workspace, setup, user)
    assert final(client, workspace, setup, versions[0]).json() == {"final": str(versions[0].pk)}
    video = VideoAsset.objects.get()
    assert video.final_version == versions[0] and video.final_by == user and video.final_at is not None
    assert final(client, workspace, setup, versions[1]).status_code == 200
    video.refresh_from_db()
    assert video.final_version == versions[1]
    assert VideoAsset.objects.filter(final_version__isnull=False).count() == 1


def test_mark_can_be_removed_but_only_from_the_final_version(client, ff, workspace, user, setup):
    versions = two_versions(client, workspace, setup, user)
    final(client, workspace, setup, versions[1])
    assert final(client, workspace, setup, versions[0], final=False).json() == {"final": None}
    assert VideoAsset.objects.get().final_version == versions[1]
    assert final(client, workspace, setup, versions[1], final=False).status_code == 200
    video = VideoAsset.objects.get()
    assert video.final_version is None and video.final_by is None and video.final_at is None


@pytest.mark.parametrize("role", ["client", "viewer", "editor", "contributor"])
def test_other_roles_get_403_on_the_server(client, ff, workspace, user, setup, role):
    versions = two_versions(client, workspace, setup, user)
    person = make_user(workspace, f"{role}@example.com", role)
    client.force_login(person)
    assert final(client, workspace, setup, versions[0]).status_code == 403
    assert VideoAsset.objects.get().final_version is None
    # They see the mark, they just cannot set it.
    client.force_login(user)
    final(client, workspace, setup, versions[0])
    client.force_login(person)
    card = client.get(url("upload_status", workspace, setup)).json()["channels"][0]
    assert card["can_mark_final"] is False and card["final"]["number"] == 1
    assert final(client, workspace, setup, versions[1], final=False).status_code == 403
    assert VideoAsset.objects.get().final_version == versions[0]


def test_custom_role_follows_its_own_approve_permission(client, ff, workspace, user, setup):
    versions = two_versions(client, workspace, setup, user)
    person = make_user(workspace, "custom@example.com", "viewer")
    role = CustomRole.objects.create(
        organization=workspace.organization, name="Approver", permissions={"approve_posts": True}
    )
    WorkspaceMembership.objects.filter(user=person).update(custom_role=role)
    client.force_login(person)
    assert final(client, workspace, setup, versions[0]).status_code == 200
    role.permissions = {"approve_posts": False}
    role.save()
    assert final(client, workspace, setup, versions[1]).status_code == 403


def test_only_converted_versions_can_be_final(client, ff, workspace, user, setup):
    versions = two_versions(client, workspace, setup, user)
    VideoVersion.objects.filter(pk=versions[1].pk).update(status="processing")
    response = final(client, workspace, setup, versions[1])
    assert response.status_code == 409 and VideoAsset.objects.get().final_version is None


def test_version_of_other_content_is_not_found(client, ff, workspace, user, setup):
    from apps.jrc_production.services import create_content

    versions = two_versions(client, workspace, setup, user)
    other = create_content(workspace, "Other reel", user)
    response = post_json(client, url("video_final", workspace, other, ("version_id", versions[0].pk)), {})
    assert response.status_code == 404
    assert post_json(client, url("video_final", workspace, setup, ("version_id", uuid.uuid4())), {}).status_code == 404


def test_stranger_anonymous_and_archived_workspace(client, ff, workspace, user, setup):
    from apps.accounts.models import User

    versions = two_versions(client, workspace, setup, user)
    client.logout()
    assert final(client, workspace, setup, versions[0]).status_code == 302
    stranger = User.objects.create_user(email="s@example.com", password="x", tos_accepted_at=timezone.now())
    client.force_login(stranger)
    assert final(client, workspace, setup, versions[0]).status_code == 403
    client.force_login(user)
    workspace.is_archived = True
    workspace.save()
    assert final(client, workspace, setup, versions[0]).status_code == 403
    assert VideoAsset.objects.get().final_version is None


def test_final_requires_csrf_token(workspace, user, setup, ff):
    from django.test import Client

    strict = Client(enforce_csrf_checks=True)
    strict.force_login(user)
    video_version = two_versions(Client(), workspace, setup, user)[0]
    response = strict.post(
        url("video_final", workspace, setup, ("version_id", video_version.pk)), "{}", content_type="application/json"
    )
    assert response.status_code == 403


def test_deleting_the_final_version_clears_the_pointer(client, ff, workspace, user, setup):
    versions = two_versions(client, workspace, setup, user)
    final(client, workspace, setup, versions[1])
    versions[1].delete()
    assert VideoAsset.objects.get().final_version is None


def test_can_mark_final_helper_matches_the_approving_team(workspace, user, setup):
    owner_membership = WorkspaceMembership.objects.get(user=user, workspace=workspace)
    assert player.can_mark_final(setup, owner_membership) is True
    assert player.can_mark_final(setup, None) is False


# --- conversion state without the upload card ------------------------------------------


def test_opening_the_tab_refreshes_unfinished_versions(client, ff, workspace, user, setup):
    client.force_login(user)
    started(client, workspace, setup)
    version = VideoVersion.objects.get()
    VideoVersion.objects.filter(pk=version.pk).update(status="processing")
    ff.versions[str(version.video.ff_asset_id)][0].update(
        processing_status="ready", files=[{"width": 1280, "height": 720, "duration_seconds": 17.0, "fps": 25.0}]
    )
    tab(client, workspace, setup)
    version.refresh_from_db()
    assert version.status == "ready" and (version.width, version.height) == (1280, 720)


def test_opening_the_tab_survives_a_broken_freeframe(client, ff, workspace, user, setup):
    client.force_login(user)
    started(client, workspace, setup)
    VideoVersion.objects.update(status="processing")
    ff.fail[("GET", "/assets/")] = 503
    response = tab(client, workspace, setup)
    assert response.status_code == 200 and b"Converting" in response.content


def test_worker_job_refreshes_every_unfinished_version_with_the_service_account(
    client, ff, workspace, user, setup, monkeypatch
):
    client.force_login(user)
    started(client, workspace, setup)
    version = VideoVersion.objects.get()
    VideoVersion.objects.filter(pk=version.pk).update(status="processing")
    ff.versions[str(version.video.ff_asset_id)][0].update(processing_status="ready")
    service_calls = []

    class ServiceClient:
        def request(self, method, path, payload=None):
            service_calls.append((method, path))
            return ff.answer(method, path, payload)

    monkeypatch.setattr(uploads, "FreeFrameClient", ServiceClient)
    assert uploads.refresh_all_unfinished() == 1
    version.refresh_from_db()
    assert version.status == "ready"
    assert service_calls == [("GET", f"/assets/{version.video.ff_asset_id}/versions")]
    assert uploads.refresh_all_unfinished() == 0  # nothing unfinished: FreeFrame is not called
    assert len(service_calls) == 1


def test_worker_task_is_skipped_without_credentials_and_registered(settings, monkeypatch):
    from apps.jrc_video import tasks

    called = []
    monkeypatch.setattr(uploads, "refresh_all_unfinished", lambda: called.append(1))
    settings.JRC_FF_SYNC_ENABLED = False
    tasks.refresh_video_statuses.task_function()
    assert called == []
    settings.JRC_FF_SYNC_ENABLED = True
    tasks.refresh_video_statuses.task_function()
    assert called == [1]
    assert tasks.REFRESH_INTERVAL_SECONDS <= 60
