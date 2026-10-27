from datetime import timedelta
from unittest.mock import patch

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
        self.user.tos_accepted_at = timezone.now()
        self.user.save(update_fields=["tos_accepted_at"])
        OrgMembership.objects.filter(user=self.user).delete()
        OrgMembership.objects.create(user=self.user, organization=self.org, org_role="owner")
        WorkspaceMembership.objects.create(user=self.user, workspace=self.workspace, workspace_role="owner")
        self.user.last_workspace_id = self.workspace.pk
        self.user.save(update_fields=["last_workspace_id"])
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
        with override_settings(APP_URL="https://public.example", JRC_BASE_PATH="/jrc", FORCE_SCRIPT_NAME="/jrc"):
            response = self.client.get(reverse("members:list"), follow=True)
        self.assertContains(response, "Copy link")
        self.assertContains(response, "https://public.example/jrc/members/invite/test-invite-token/accept/")

    def test_non_admin_does_not_receive_pending_invitation_link(self):
        member = User.objects.create_user(email="member@example.com", password="test-pass-123")
        member.tos_accepted_at = timezone.now()
        member.save(update_fields=["tos_accepted_at"])
        OrgMembership.objects.filter(user=member).delete()
        OrgMembership.objects.create(user=member, organization=self.org, org_role="member")
        WorkspaceMembership.objects.create(user=member, workspace=self.workspace, workspace_role="viewer")
        member.last_workspace_id = self.workspace.pk
        member.save(update_fields=["last_workspace_id"])
        self.client.force_login(member)
        response = self.client.get(reverse("members:list"), follow=True)
        self.assertNotContains(response, "test-invite-token")
        self.assertNotContains(response, "data-copy-text=")

    def test_htmx_create_and_resend_render_copy_link_without_missing_attribute(self):
        self.client.force_login(self.user)
        with override_settings(APP_URL="https://public.example", JRC_BASE_PATH="/jrc", FORCE_SCRIPT_NAME="/jrc"):
            with patch("apps.members.views.services.create_invitation", return_value=self.invite):
                created = self.client.post(
                    reverse("members:invite"),
                    {"email": self.invite.email},
                    HTTP_HX_REQUEST="true",
                )
            with patch("apps.members.views.services.resend_invitation", return_value=None):
                resent = self.client.post(
                    reverse("members:resend_invite", kwargs={"invitation_id": self.invite.pk}),
                    HTTP_HX_REQUEST="true",
                )

        expected = "https://public.example/jrc/members/invite/test-invite-token/accept/"
        self.assertEqual(created.status_code, 200)
        self.assertContains(created, expected)
        self.assertEqual(resent.status_code, 200)
        self.assertContains(resent, expected)
