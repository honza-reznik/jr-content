import pytest
from django.test import override_settings

from apps.workspaces.models import Workspace


@pytest.fixture(autouse=True)
def _social_provider_test_mode():
    # Exercise the real JR Content configuration, independently of upstream tests.
    with override_settings(JRC_SOCIAL_PROVIDERS_ENABLED=False):
        yield


@pytest.fixture
def workspace(organization):
    return Workspace.objects.create(name="Manual channels", organization=organization)
