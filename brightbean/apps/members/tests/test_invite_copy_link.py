from datetime import timedelta

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.members.models import Invitation, OrgMembership, WorkspaceMembership
from apps.organizations.models import Organization
from apps.workspaces.models import Workspace


class InvitationCopyLinkTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Test Org")
        self.workspace = Workspace.objects.create(organization=self.org, name="Test Workspace")
        self.user = User.objects.create_user(email="owner@example.com", password="test-pass-123")
        OrgMembership.objects.filter(user=self.user).delete()
        OrgMembership.objects.create(user=self.user, organization=self.org, org_role="owner")
        WorkspaceMembership.objects.create(user=self.user, workspace=self.workspace, workspace_role="owner")
        self.invite = Invitation.objects.create(
            organization=self.org,
            email="invitee@example.com",
            org_role="member",
            token="test-invite-token",
            invited_by=self.user,
            expires_at=timezone.now() + timedelta(days=7),
            workspace_assignments=[],
        )

    def test_pending_invitation_shows_public_copy_link_only_to_admin(self):
        self.client.force_login(self.user)
        with override_settings(APP_URL="https://public.example", FORCE_SCRIPT_NAME="/jrc"):
            response = self.client.get(reverse("members:list"))
        self.assertContains(response, "Copy link")
        self.assertContains(response, "https://public.example/jrc/members/invite/test-invite-token/accept/")

    def test_non_admin_does_not_receive_pending_invitation_link(self):
        member = User.objects.create_user(email="member@example.com", password="test-pass-123")
        OrgMembership.objects.filter(user=member).delete()
        OrgMembership.objects.create(user=member, organization=self.org, org_role="member")
        self.client.force_login(member)
        response = self.client.get(reverse("members:list"))
        self.assertNotContains(response, "test-invite-token")
        self.assertNotContains(response, "Copy link")
