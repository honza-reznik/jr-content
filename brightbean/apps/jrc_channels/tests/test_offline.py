import socket
from datetime import timedelta
from unittest.mock import patch

import httpx
import pytest
from background_task.models import Task
from django.conf import settings
from django.urls import reverse
from django.utils import timezone

from apps.analytics.tasks import backfill_account_analytics, retry_youtube_post_analytics, sync_all_account_analytics
from apps.composer.models import PlatformPost, Post
from apps.inbox.models import InboxMessage, InboxReply
from apps.inbox.services import create_reply_draft, send_reply_now
from apps.inbox.tasks import InboxSyncEngine
from apps.jrc_channels.services import create_manual_channel, set_channel_enabled
from apps.members.models import OrgMembership, WorkspaceMembership
from apps.publisher.engine import PublishEngine
from apps.social_accounts.models import AnalyticsPlatformConfig
from apps.social_accounts.tasks import check_social_account_health, schedule_all_health_checks
from apps.social_accounts.webhooks import retry_failed_subscription
from apps.social_accounts.youtube_compliance import (
    _delete_expired,
    check_connection,
    enforce_youtube_retention,
    purge_connection,
    purge_organization,
)
from providers import PROVIDER_REGISTRY, ProvidersDisabledError, get_provider


@pytest.fixture
def no_network(monkeypatch, db):
    """A failed network attempt must escape broad upstream exception handlers.

    The DB fixture opens the PostgreSQL connection before the guard is armed;
    all tested paths use that connection in the current thread.
    """
    import requests

    def forbidden(*args, **kwargs):
        pytest.fail("Attempted an outgoing connection with social providers disabled")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(httpx.Client, "send", forbidden)
    monkeypatch.setattr(httpx.AsyncClient, "send", forbidden)
    monkeypatch.setattr(requests.Session, "send", forbidden)


@pytest.mark.parametrize("entrypoint", [socket.getaddrinfo, socket.create_connection])
def test_network_guard_blocks_dns_and_connection_helpers(entrypoint, no_network):
    # Look up the patched function by name, rather than the captured original.
    with pytest.raises(pytest.fail.Exception, match="Attempted an outgoing connection"):
        getattr(socket, entrypoint.__name__)("example.invalid", 443)


@pytest.mark.parametrize("platform", [*PROVIDER_REGISTRY, "custom", "unknown"])
def test_switch_blocks_before_provider_construction(platform, no_network):
    assert settings.JRC_SOCIAL_PROVIDERS_ENABLED is False
    with (
        patch.dict(PROVIDER_REGISTRY, {platform: lambda **kwargs: pytest.fail("Provider was constructed")}),
        pytest.raises(ProvidersDisabledError),
    ):
        get_provider(platform, {})
    assert issubclass(ProvidersDisabledError, ValueError)


def test_all_background_paths_and_composer_are_offline(
    workspace, org_owner, user, client, no_network, django_capture_on_commit_callbacks
):
    platforms = ["instagram", "facebook", "linkedin_personal", "linkedin_company", "tiktok", "youtube", "custom"]
    accounts = {p: create_manual_channel(workspace, p, "Ella") for p in platforms}
    assert not Task.objects.filter(task_name="apps.analytics.tasks.backfill_account_analytics").exists()
    post = Post.objects.create(workspace=workspace, author=user, scheduled_at=timezone.now() - timedelta(minutes=1))
    posts = []
    for account in accounts.values():
        posts.append(PlatformPost.objects.create(post=post, social_account=account, status="scheduled"))
        retry_post = Post.objects.create(workspace=workspace, author=user)
        posts.append(
            PlatformPost.objects.create(
                post=retry_post,
                social_account=account,
                status="scheduled",
                scheduled_at=timezone.now() - timedelta(hours=1),
                retry_count=1,
                next_retry_at=timezone.now() - timedelta(minutes=1),
            )
        )

    schedule_all_health_checks.now()
    assert Task.objects.filter(task_name="apps.social_accounts.tasks.check_social_account_health").count() == len(
        accounts
    )
    for account in accounts.values():
        check_social_account_health.now(str(account.pk))
        backfill_account_analytics.now(str(account.pk))
        retry_failed_subscription(account)
    InboxSyncEngine().sync_all()
    sync_all_account_analytics.now()
    retry_youtube_post_analytics.now(str(accounts["youtube"].pk), timezone.now().date().isoformat(), 1)
    check_connection(accounts["youtube"])

    for platform in platforms:
        config, _ = AnalyticsPlatformConfig.objects.update_or_create(platform=platform, defaults={"is_enabled": False})
        with django_capture_on_commit_callbacks(execute=True):
            config.is_enabled = True
            config.save(update_fields=["is_enabled"])
    assert Task.objects.filter(task_name="apps.analytics.tasks.backfill_account_analytics").exists()
    # Execute the actual backfill bodies queued by the platform-enable signal.
    for account in accounts.values():
        backfill_account_analytics.now(str(account.pk))

    engine = PublishEngine()
    assert engine._get_due_platform_posts() == []
    assert engine.poll_and_publish() == 0
    engine.confirm_pending_publishes()
    for pp in posts:
        pp.refresh_from_db()
        assert pp.status == "scheduled"
        assert pp.published_at is None
        assert pp.retry_count in (0, 1)

    WorkspaceMembership.objects.create(user=user, workspace=workspace, workspace_role="owner")
    client.force_login(user)
    # Pinterest has no manual channel. Keep exercising its original endpoint
    # using a legacy OAuth account, with the same provider/network guards.
    from apps.social_accounts.models import SocialAccount

    pinterest = SocialAccount(
        workspace=workspace, platform="pinterest", account_name="Legacy", account_platform_id="fake-pinterest"
    )
    pinterest._skip_analytics_backfill = True
    pinterest.save()
    for platform, endpoint in [("pinterest", "pinterest_boards"), ("tiktok", "tiktok_creator_info")]:
        account = pinterest if platform == "pinterest" else accounts[platform]
        response = client.get(
            reverse(f"composer:{endpoint}", kwargs={"workspace_id": workspace.pk, "account_id": account.pk})
        )
        assert response.status_code == 200
        assert response.json()["available"] is False
        assert response.json()["error"]

    message = InboxMessage.objects.create(
        workspace=workspace,
        social_account=accounts["instagram"],
        platform_message_id="fake-comment",
        sender_name="Test person",
        received_at=timezone.now(),
    )
    reply = create_reply_draft(message=message, body="Thank you", author=user)
    with pytest.raises(ProvidersDisabledError):
        send_reply_now(reply)
    reply.refresh_from_db()
    assert reply.status == InboxReply.Status.FAILED
    assert not reply.platform_reply_id
    assert reply.sent_at is None
    response = client.post(f"/workspace/{workspace.pk}/inbox/{message.pk}/reply/", {"body": "Thank you from the web"})
    assert response.status_code == 200
    assert response["HX-Reply-Failed"] == "1"
    assert not InboxReply.objects.filter(status="sent").exists()

    for account in accounts.values():
        account.refresh_from_db()
        assert account.connection_status == "connected"
        assert not account.last_error
        assert not account.oauth_access_token
        assert not account.oauth_refresh_token


def test_publisher_still_selects_upstream_accounts(workspace, user):
    from apps.social_accounts.models import SocialAccount

    account = SocialAccount(workspace=workspace, platform="facebook", account_name="Fake", account_platform_id="fake")
    account._skip_analytics_backfill = True
    account.save()
    post = Post.objects.create(workspace=workspace, author=user, scheduled_at=timezone.now() - timedelta(minutes=1))
    pp = PlatformPost.objects.create(post=post, social_account=account, status="scheduled")
    assert PublishEngine()._get_due_platform_posts() == [pp]


def test_old_local_youtube_channel_is_not_subject_to_oauth_retention(workspace, user, no_network):
    account = create_manual_channel(workspace, "youtube", "Ella")
    account.youtube_verification_started_at = timezone.now() - timedelta(days=40)
    account.save(update_fields=["youtube_verification_started_at"])
    post = Post.objects.create(workspace=workspace, author=user)
    pp = PlatformPost.objects.create(post=post, social_account=account, status="published", published_at=timezone.now())
    check_connection(account)
    account.refresh_from_db()
    assert account.connection_status == "connected"
    assert PlatformPost.objects.filter(pk=pp.pk).exists()


@pytest.mark.parametrize("enabled", [True, False])
@pytest.mark.parametrize("providers_enabled", [True, False])
@pytest.mark.parametrize("cleanup", ["organization", "connection", "generation", "expired", "verification", "daily"])
def test_all_youtube_cleanup_paths_preserve_manual_content(
    workspace, user, no_network, settings, enabled, providers_enabled, cleanup
):
    account = create_manual_channel(workspace, "youtube", "Ella")
    set_channel_enabled(account, enabled=enabled)
    now = timezone.now()
    account.youtube_verification_started_at = now - timedelta(days=400)
    account.youtube_authorized_by = user
    account.save(update_fields=["youtube_verification_started_at", "youtube_authorized_by"])
    post = Post.objects.create(workspace=workspace, author=user, caption="Authored content")
    pp = PlatformPost.objects.create(
        post=post,
        social_account=account,
        status="published",
        platform_post_id="manual-video",
        platform_extra={"snippet": {"title": "Local authored data"}},
    )
    settings.JRC_SOCIAL_PROVIDERS_ENABLED = providers_enabled
    settings.YOUTUBE_COMPLIANCE_ENFORCEMENT = True
    # An explicitly supplied provider must not be consulted for a local channel.
    from unittest.mock import Mock

    provider = Mock()
    provider.get_profile.side_effect = AssertionError("Local channel must never be verified")
    if cleanup == "organization":
        purge_organization(workspace.organization)
    elif cleanup in {"connection", "generation"}:
        purge_connection(account, check_generation=cleanup == "generation")
    elif cleanup == "expired":
        _delete_expired(account, now)
    elif cleanup == "verification":
        check_connection(account, provider=provider, now=now)
    else:
        enforce_youtube_retention.now()
    account.refresh_from_db()
    pp.refresh_from_db()
    post.refresh_from_db()
    assert account.account_name == "Ella"
    assert account.youtube_data_generation == 0
    assert account.jrc_channel.is_enabled is enabled
    assert account.connection_status == ("connected" if enabled else "disconnected")
    assert post.caption == "Authored content"
    assert pp.platform_extra == {"snippet": {"title": "Local authored data"}}
    assert not provider.mock_calls


def test_organization_deletion_request_preserves_local_youtube(workspace, org_owner, user, client, no_network):
    from apps.organizations.tasks import execute_scheduled_org_deletion

    # User creation provisions another org automatically. Global settings use
    # the first org membership, so give this request one unambiguous target.
    OrgMembership.objects.filter(user=user).exclude(organization=workspace.organization).delete()
    WorkspaceMembership.objects.create(user=user, workspace=workspace, workspace_role="owner")
    user.last_workspace_id = workspace.pk
    user.save(update_fields=["last_workspace_id"])
    account = create_manual_channel(workspace, "youtube", "Ella")
    post = Post.objects.create(workspace=workspace, author=user, caption="Keep during grace period")
    pp = PlatformPost.objects.create(post=post, social_account=account, status="scheduled")
    client.force_login(user)
    response = client.post(reverse("organizations:settings"), {"action": "delete_organization"})
    assert response.status_code == 302
    org = workspace.organization
    org.refresh_from_db()
    assert org.is_deletion_pending
    assert Task.objects.filter(task_name="apps.organizations.tasks.execute_scheduled_org_deletion").exists()
    # Execute the queued body during the grace period, then cancel the request.
    execute_scheduled_org_deletion.now(str(org.pk))
    response = client.post(reverse("organizations:settings"), {"action": "cancel_deletion"})
    assert response.status_code == 302
    org.refresh_from_db()
    assert not org.is_deletion_pending
    execute_scheduled_org_deletion.now(str(org.pk))
    account.refresh_from_db()
    pp.refresh_from_db()
    post.refresh_from_db()
    assert account.jrc_channel.is_enabled
    assert pp.status == "scheduled"
    assert post.caption == "Keep during grace period"
