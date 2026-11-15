"""JR Content P2-FABLE: the version diff refuses a non-numeric version instead of failing."""

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.composer.models import Post
from apps.members.models import OrgMembership, WorkspaceMembership
from apps.organizations.models import Organization
from apps.workspaces.models import Workspace


class VersionDiffInputTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="diff@example.com", password="testpass123", tos_accepted_at=timezone.now()
        )
        WorkspaceMembership.objects.filter(user=self.user).delete()
        self.org = Organization.objects.create(name="Diff Org")
        self.workspace = Workspace.objects.create(organization=self.org, name="Diff WS")
        OrgMembership.objects.create(user=self.user, organization=self.org, org_role="owner")
        WorkspaceMembership.objects.create(user=self.user, workspace=self.workspace, workspace_role="editor")
        self.post = Post.objects.create(workspace=self.workspace, author=self.user, caption="hello")
        self.url = reverse(
            "approvals:version_diff", kwargs={"workspace_id": self.workspace.id, "post_id": self.post.id}
        )
        self.client.force_login(self.user)

    def test_non_numeric_version_is_a_bad_request(self):
        response = self.client.get(self.url, {"v1": "abc", "v2": "1"})
        self.assertEqual(response.status_code, 400)

    def test_numeric_versions_still_render(self):
        response = self.client.get(self.url, {"v1": "1", "v2": "2"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get(self.url).status_code, 200)
