import pytest

from apps.composer.models import PlatformPost, Post
from apps.jrc_channels.services import create_manual_channel
from apps.members.models import WorkspaceMembership
from apps.workspaces.models import Workspace


@pytest.fixture(autouse=True)
def local_storage(settings):
    settings.STORAGES = {
        **settings.STORAGES,
        "default": {"BACKEND": "django.core.files.storage.InMemoryStorage"},
    }
    settings.JRC_SOCIAL_PROVIDERS_ENABLED = False


@pytest.fixture
def workspace(organization, user):
    workspace = Workspace.objects.create(organization=organization, name="Thumbnail test")
    WorkspaceMembership.objects.create(workspace=workspace, user=user, workspace_role="owner")
    return workspace


@pytest.fixture
def post(workspace, user):
    return Post.objects.create(workspace=workspace, author=user, title="Plant care")


@pytest.fixture
def variant(workspace, post):
    account = create_manual_channel(workspace, "instagram", "Plants")
    return PlatformPost.objects.create(post=post, social_account=account)
