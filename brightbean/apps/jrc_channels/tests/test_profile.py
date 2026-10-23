import pytest
from django.test import Client, override_settings
from django.urls import reverse

from apps.composer.models import PlatformPost, Post
from apps.jrc_channels.services import create_manual_channel
from apps.jrc_ui.features import MVP_FEATURES
from apps.members.models import CustomRole, WorkspaceMembership
from apps.social_accounts.models import SocialAccount
from apps.workspaces.models import Workspace

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def mvp_ui():
    with override_settings(JRC_FEATURES=MVP_FEATURES):
        yield


@pytest.fixture
def editor(client, user, workspace):
    WorkspaceMembership.objects.create(user=user, workspace=workspace, workspace_role="owner")
    client.force_login(user)
    return client


def url(workspace, action="profile", account=None):
    kwargs = {"workspace_id": workspace.pk}
    if account:
        kwargs["account_id"] = account.pk
    return reverse(f"jrc_channels:{action}", kwargs=kwargs)


def test_empty_profile_and_discovery(editor, workspace):
    response = editor.get(url(workspace))
    assert response.status_code == 200
    assert b"No channels yet" in response.content
    assert b"Add a channel" in response.content
    response = editor.get(reverse("calendar:calendar", kwargs={"workspace_id": workspace.pk}))
    assert url(workspace).encode() in response.content
    assert b"Client profile" in response.content
    response = editor.get(reverse("workspaces:settings", kwargs={"workspace_id": workspace.pk}))
    assert url(workspace).encode() in response.content


def test_profile_requires_login(client, workspace):
    response = client.get(url(workspace))
    assert response.status_code == 302
    assert "/accounts/login/" in response.url


@pytest.mark.parametrize("platform", ["instagram", "custom"])
def test_create_channel_without_network(editor, workspace, platform, no_network):
    response = editor.post(
        url(workspace),
        {
            "platform": platform,
            "name": "Forendors" if platform == "custom" else "Ella",
            "profile_url": "https://example.invalid/ella",
        },
    )
    assert response.status_code == 302
    account = workspace.social_accounts.get()
    assert account.jrc_channel.display_name == account.account_name
    assert account.jrc_channel.profile_url == "https://example.invalid/ella"
    assert account.jrc_channel.is_enabled
    assert account.jrc_channel.kind == ("custom" if platform == "custom" else "manual")
    assert not account.oauth_access_token


@pytest.mark.parametrize(
    "data,error",
    [
        ({"name": "", "platform": "instagram"}, "This field is required"),
        ({"name": "x" * 101, "platform": "custom"}, "at most 100 characters"),
        ({"name": "Ella", "platform": "mastodon"}, "Select a valid choice"),
        ({"name": "Ella", "platform": "custom", "profile_url": "javascript:alert(1)"}, "Enter a valid URL"),
        ({"name": "Ella", "platform": "custom", "profile_url": "ftp://example.invalid/ella"}, "Use a link"),
        ({"name": "Ella", "platform": "custom", "profile_url": "https://"}, "Enter a valid URL"),
    ],
)
def test_validation_preserves_input_and_no_partial_creation(editor, workspace, data, error, no_network):
    response = editor.post(url(workspace), data)
    assert response.status_code == 200
    assert error.encode() in response.content
    assert response.context["add_form"].is_bound
    assert not workspace.social_accounts.exists()


def test_edit_custom_name_and_link_syncs_with_post_selector(editor, workspace, no_network):
    account = create_manual_channel(workspace, "custom", "Forendors")
    response = editor.post(
        url(workspace, "edit", account), {"name": "Ella members", "profile_url": "https://example.invalid/members"}
    )
    assert response.status_code == 302
    account.refresh_from_db()
    assert account.account_name == account.jrc_channel.display_name == "Ella members"
    assert account.jrc_channel.profile_url == "https://example.invalid/members"
    assert not account.oauth_access_token
    response = editor.post(url(workspace, "edit", account), {"name": "Ella members", "profile_url": ""})
    assert response.status_code == 302
    account.refresh_from_db()
    assert account.jrc_channel.profile_url == ""


def test_invalid_edit_keeps_existing_values_and_opens_form(editor, workspace):
    account = create_manual_channel(workspace, "custom", "Forendors", profile_url="https://example.invalid/original")
    response = editor.post(url(workspace, "edit", account), {"name": "New name", "profile_url": "bad link"})
    assert response.status_code == 200
    assert response.context["channels"][0]["is_editing"]
    assert response.context["channels"][0]["form"]["name"].value() == "New name"
    account.refresh_from_db()
    assert account.account_name == "Forendors"
    assert account.jrc_channel.profile_url.endswith("/original")


def test_off_and_on_preserve_post_and_channel_details(editor, workspace, user, no_network):
    account = create_manual_channel(workspace, "custom", "Forendors", profile_url="https://example.invalid/ella")
    post = Post.objects.create(workspace=workspace, author=user, caption="Keep my content")
    variant = PlatformPost.objects.create(post=post, social_account=account)
    for enabled in ["false", "true", "true"]:
        response = editor.post(url(workspace, "toggle", account), {"enabled": enabled})
        assert response.status_code == 302
        account.refresh_from_db()
        assert account.jrc_channel.is_enabled == (enabled == "true")
        assert account.connection_status == ("connected" if enabled == "true" else "disconnected")
        assert account.jrc_channel.profile_url == "https://example.invalid/ella"
        assert PlatformPost.objects.filter(pk=variant.pk).exists()
        assert Post.objects.get(pk=post.pk).caption == "Keep my content"
    editor.post(url(workspace, "toggle", account), {"enabled": "invalid"})
    account.refresh_from_db()
    assert account.jrc_channel.is_enabled


@pytest.mark.parametrize("role", ["editor", "contributor", "client", "viewer"])
def test_read_only_roles_cannot_mutate_channels(editor, workspace, user, role):
    membership = WorkspaceMembership.objects.get(user=user, workspace=workspace)
    membership.workspace_role = role
    membership.save()
    account = create_manual_channel(workspace, "custom", "Forendors")
    response = editor.get(url(workspace))
    assert response.status_code == 200
    assert b"Add a channel" not in response.content
    assert b"Edit channel" not in response.content
    assert b"Turn off" not in response.content
    for target, data in [
        (url(workspace), {"platform": "instagram", "name": "Other"}),
        (url(workspace, "edit", account), {"name": "Changed"}),
        (url(workspace, "toggle", account), {"enabled": "false"}),
    ]:
        assert editor.post(target, data).status_code == 403
    account.refresh_from_db()
    assert account.account_name == "Forendors"
    assert account.jrc_channel.is_enabled


@pytest.mark.parametrize("allowed", [False, True])
def test_custom_permissions_are_respected(editor, workspace, user, allowed):
    membership = WorkspaceMembership.objects.get(user=user, workspace=workspace)
    membership.custom_role = CustomRole.objects.create(
        organization=workspace.organization,
        name="Channel operator",
        permissions={"manage_social_accounts": allowed},
    )
    membership.save()
    response = editor.post(url(workspace), {"platform": "custom", "name": "Forendors"})
    assert response.status_code == (302 if allowed else 403)


def test_cannot_access_other_workspace_or_edit_other_channel(editor, workspace, organization):
    other = Workspace.objects.create(organization=organization, name="Private client")
    account = create_manual_channel(other, "custom", "Private channel")
    assert editor.get(url(other)).status_code in {403, 404}
    assert editor.post(url(other), {"platform": "custom", "name": "Intruder"}).status_code in {403, 404}
    assert editor.post(url(workspace, "edit", account), {"name": "Intruder"}).status_code == 404
    assert editor.post(url(workspace, "toggle", account), {"enabled": "false"}).status_code == 404
    assert b"Private channel" not in editor.get(url(workspace)).content
    account.refresh_from_db()
    assert account.account_name == "Private channel"
    assert account.jrc_channel.is_enabled


def test_get_does_not_mutate_and_csrf_is_required(editor, workspace, user):
    account = create_manual_channel(workspace, "custom", "Forendors")
    assert editor.get(url(workspace, "edit", account)).status_code == 405
    assert editor.get(url(workspace, "toggle", account)).status_code == 405
    secured = Client(enforce_csrf_checks=True)
    secured.force_login(user)
    assert secured.post(url(workspace), {"platform": "custom", "name": "Test"}).status_code == 403
    assert secured.post(url(workspace, "toggle", account), {"enabled": "false"}).status_code == 403
    assert secured.post(url(workspace, "edit", account), {"name": "Changed"}).status_code == 403


def test_upstream_oauth_account_cannot_be_changed_via_manual_profile(editor, workspace):
    account = SocialAccount.objects.create(
        workspace=workspace, platform="instagram", account_name="OAuth", account_platform_id="oauth"
    )
    assert editor.post(url(workspace, "toggle", account), {"enabled": "false"}).status_code == 404
    assert editor.post(url(workspace, "edit", account), {"name": "Changed"}).status_code == 404


def test_names_are_escaped(editor, workspace):
    create_manual_channel(workspace, "custom", '<script>alert("test")</script>')
    response = editor.get(url(workspace))
    assert b"<script>alert" not in response.content
    assert b"&lt;script&gt;" in response.content


def test_profile_link_requires_https():
    """Review F2-B08: odkaz na profil kanálu jen přes HTTPS."""
    from apps.jrc_channels.forms import ChannelDetailsForm

    insecure = ChannelDetailsForm(data={"name": "Ella", "profile_url": "http://example.test/ella"})
    assert not insecure.is_valid()
    assert "profile_url" in insecure.errors
    secure = ChannelDetailsForm(data={"name": "Ella", "profile_url": "https://example.test/ella"})
    assert secure.is_valid(), secure.errors
