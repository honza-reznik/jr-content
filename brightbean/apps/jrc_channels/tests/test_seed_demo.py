from io import StringIO

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.composer.models import PlatformPost, Post
from apps.jrc_channels.management.commands.jrc_seed_demo import DEMO_USER_ID, DEMO_WORKSPACE_ID
from apps.jrc_channels.services import set_channel_enabled
from apps.members.models import WorkspaceMembership
from apps.social_accounts.models import SocialAccount
from apps.workspaces.models import Workspace

pytestmark = pytest.mark.django_db


def seed(**kwargs):
    call_command("jrc_seed_demo", stdout=StringIO(), **kwargs)
    return Workspace.objects.get(pk=DEMO_WORKSPACE_ID)


def test_demo_is_offline_fictitious_and_idempotent(user, no_network):
    workspace = seed(owner_email=user.email)
    assert workspace.name == "Ella"
    assert WorkspaceMembership.objects.get(workspace=workspace, user=user).workspace_role == "owner"
    demo_user = get_user_model().objects.get(pk=DEMO_USER_ID)
    assert demo_user.email.endswith("@example.invalid")
    assert not demo_user.has_usable_password()
    accounts = list(workspace.social_accounts.select_related("jrc_channel").order_by("platform"))
    assert [account.platform for account in accounts] == ["custom", "instagram"]
    assert all("example.invalid" in account.jrc_channel.profile_url for account in accounts)
    assert all(not account.oauth_access_token for account in accounts)
    original_ids = {account.pk for account in accounts}
    account = accounts[0]
    account.account_name = "Renamed by owner"
    account.save()
    profile = account.jrc_channel
    profile.display_name = "Member club"
    profile.profile_url = "https://example.invalid/new-link"
    profile.save()
    set_channel_enabled(account, enabled=False)
    post = Post.objects.create(workspace=workspace, author=user, caption="Keep this")
    variant = PlatformPost.objects.create(post=post, social_account=account)
    workspace.name = "Ella edited"
    workspace.save()
    seed(owner_email=user.email)
    seed()
    account.refresh_from_db()
    workspace.refresh_from_db()
    assert workspace.name == "Ella edited"
    assert set(workspace.social_accounts.values_list("pk", flat=True)) == original_ids
    assert account.account_name == "Renamed by owner"
    assert account.jrc_channel.display_name == "Member club"
    assert account.jrc_channel.profile_url == "https://example.invalid/new-link"
    assert not account.jrc_channel.is_enabled
    assert PlatformPost.objects.filter(pk=variant.pk, post__caption="Keep this").exists()


def test_demo_never_reuses_existing_client_by_name(workspace, no_network):
    workspace.name = "Ella"
    workspace.save()
    demo = seed()
    assert demo.pk != workspace.pk
    assert not workspace.social_accounts.exists()


def test_unknown_owner_rolls_back_without_creating_demo(no_network):
    with pytest.raises(CommandError, match="No active local user"):
        seed(owner_email="nobody@example.invalid")
    assert not Workspace.objects.filter(pk=DEMO_WORKSPACE_ID).exists()
    assert not get_user_model().objects.filter(pk=DEMO_USER_ID).exists()
    assert not SocialAccount.objects.exists()


def test_demo_email_collision_does_not_take_over_existing_user(user, no_network):
    user.email = "ella-demo@example.invalid"
    user.save()
    with pytest.raises(CommandError, match="already in use"):
        seed()
    assert not Workspace.objects.filter(pk=DEMO_WORKSPACE_ID).exists()
    user.refresh_from_db()
    assert user.has_usable_password()
