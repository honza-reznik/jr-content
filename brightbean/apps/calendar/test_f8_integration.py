"""F8 calendar pipeline, enabled-channel and personal waiting queue coverage."""

from datetime import timedelta

from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.approvals.models import ApprovalAction
from apps.calendar.models import PostingSlot
from apps.calendar.views import _get_filtered_platform_posts, _get_filtered_posts
from apps.composer.models import PlatformPost
from apps.jrc_channels.services import create_manual_channel, set_channel_enabled
from apps.jrc_production.services import create_content
from apps.members.models import OrgMembership, WorkspaceMembership
from apps.organizations.models import Organization
from apps.social_accounts.models import SocialAccount
from apps.workspaces.models import Workspace


class F8CalendarIntegrationTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            email="f8-owner@example.test", password="testpass123", tos_accepted_at=timezone.now()
        )
        self.client_user = User.objects.create_user(
            email="f8-client@example.test", password="testpass123", tos_accepted_at=timezone.now()
        )
        self.organization = Organization.objects.create(name="F8 test org")
        self.workspace = Workspace.objects.create(organization=self.organization, name="F8 test client")
        OrgMembership.objects.create(
            user=self.owner, organization=self.organization, org_role=OrgMembership.OrgRole.OWNER
        )
        WorkspaceMembership.objects.create(
            user=self.owner, workspace=self.workspace, workspace_role=WorkspaceMembership.WorkspaceRole.OWNER
        )
        WorkspaceMembership.objects.create(
            user=self.client_user,
            workspace=self.workspace,
            workspace_role=WorkspaceMembership.WorkspaceRole.CLIENT,
        )
        self.enabled_account = create_manual_channel(self.workspace, "instagram", "Enabled IG")
        self.disabled_account = create_manual_channel(self.workspace, "youtube", "Hidden YT")
        set_channel_enabled(self.disabled_account, enabled=False)

    def _content(self, title, status, *, scheduled=False):
        post = create_content(self.workspace, title, self.owner)
        child = post.platform_posts.get(social_account=self.enabled_account)
        child.status = status
        if scheduled:
            child.scheduled_at = timezone.now() + timedelta(days=2)
        child.save(update_fields=["status", "scheduled_at", "updated_at"])
        post.refresh_from_db()
        return post

    def test_calendar_chips_use_pipeline_stage_detail_link_and_enabled_channels(self):
        post = self._content("Calendar pipeline item", PlatformPost.Status.SCHEDULED, scheduled=True)
        schedule_date = post.platform_posts.get(social_account=self.enabled_account).scheduled_at.date().isoformat()
        PlatformPost.objects.create(
            post=post,
            social_account=self.disabled_account,
            status=PlatformPost.Status.SCHEDULED,
            scheduled_at=timezone.now() + timedelta(days=2),
        )
        unprofiled_account = SocialAccount.objects.create(
            workspace=self.workspace,
            platform="facebook",
            account_platform_id="legacy-unprofiled",
            account_name="Legacy account without channel profile",
            connection_status=SocialAccount.ConnectionStatus.CONNECTED,
        )
        PlatformPost.objects.create(
            post=post,
            social_account=unprofiled_account,
            status=PlatformPost.Status.SCHEDULED,
            scheduled_at=timezone.now() + timedelta(days=2),
        )
        slot_day = post.platform_posts.get(social_account=self.enabled_account).scheduled_at.weekday()
        PostingSlot.objects.create(social_account=self.enabled_account, day_of_week=slot_day, time="09:00")
        PostingSlot.objects.create(social_account=self.disabled_account, day_of_week=slot_day, time="10:00")
        PostingSlot.objects.create(social_account=unprofiled_account, day_of_week=slot_day, time="11:00")
        url = reverse("calendar:calendar", args=[self.workspace.pk])
        self.client.force_login(self.owner)

        detail_url = reverse("jrc_production:content_detail", args=[self.workspace.pk, post.pk])
        for view in ("month", "week", "day", "list"):
            with self.subTest(view=view):
                response = self.client.get(url, {"mode": "calendar", "view": view, "date": schedule_date})
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, "Ready to publish")
                self.assertContains(response, detail_url)
                self.assertIn(self.enabled_account, response.context["social_accounts"])
                self.assertNotIn(self.disabled_account, response.context["social_accounts"])
                self.assertNotIn(unprofiled_account, response.context["social_accounts"])
                self.assertNotIn(self.disabled_account, response.context["channels_with_posts"])
                self.assertNotIn(unprofiled_account, response.context["channels_with_posts"])
                if view == "month":
                    visible_ids = {
                        pp.social_account_id
                        for week in response.context["calendar_weeks"]
                        for day in week
                        for pp in day["posts"]
                    }
                elif view == "week":
                    visible_ids = {
                        pp.social_account_id
                        for _, cells in response.context["week_slots"]
                        for cell in cells
                        for pp in cell["posts"]
                    }
                elif view == "day":
                    visible_ids = {
                        pp.social_account_id for cell in response.context["day_slots"] for pp in cell["posts"]
                    }
                    visible_slot_ids = {
                        slot["account"].pk for cell in response.context["day_slots"] for slot in cell["slots"]
                    }
                    self.assertIn(self.enabled_account.pk, visible_slot_ids)
                    self.assertNotIn(self.disabled_account.pk, visible_slot_ids)
                    self.assertNotIn(unprofiled_account.pk, visible_slot_ids)
                else:
                    visible_ids = {
                        pp.social_account_id for row in response.context["posts"] for pp in row.platform_posts.all()
                    }
                self.assertIn(self.enabled_account.pk, visible_ids)
                self.assertNotIn(self.disabled_account.pk, visible_ids)
                self.assertNotIn(unprofiled_account.pk, visible_ids)

        filtered = self.client.get(
            url,
            {"mode": "calendar", "view": "list", "date": schedule_date, "channel": str(self.enabled_account.pk)},
        )
        self.assertEqual(filtered.status_code, 200)
        self.assertEqual([row.pk for row in filtered.context["posts"]], [post.pk])
        filtered_hidden = self.client.get(
            url,
            {"mode": "calendar", "view": "list", "date": schedule_date, "channel": str(self.disabled_account.pk)},
        )
        self.assertEqual(filtered_hidden.status_code, 200)
        self.assertEqual(filtered_hidden.context["posts"], [])

    def test_waiting_for_me_is_workspace_and_role_scoped(self):
        self._content("Team review item", PlatformPost.Status.PENDING_REVIEW)
        client_changes = self._content("Client change item", PlatformPost.Status.CHANGES_REQUESTED)
        self._content("Client approval item", PlatformPost.Status.PENDING_CLIENT)
        disabled_waiting = self._content("Disabled channel approval item", PlatformPost.Status.DRAFT)
        PlatformPost.objects.create(
            post=disabled_waiting,
            social_account=self.disabled_account,
            status=PlatformPost.Status.PENDING_CLIENT,
        )
        other_org = Organization.objects.create(name="F8 second org")
        other_workspace = Workspace.objects.create(organization=other_org, name="F8 second client")
        WorkspaceMembership.objects.create(
            user=self.owner,
            workspace=other_workspace,
            workspace_role=WorkspaceMembership.WorkspaceRole.OWNER,
        )
        other_account = create_manual_channel(other_workspace, "instagram", "Other workspace IG")
        foreign_post = create_content(other_workspace, "Foreign review item must stay hidden", self.owner)
        foreign_child = foreign_post.platform_posts.get(social_account=other_account)
        foreign_child.status = PlatformPost.Status.PENDING_REVIEW
        foreign_child.save(update_fields=["status", "updated_at"])
        ApprovalAction.objects.create(
            post=client_changes,
            user=self.client_user,
            action=ApprovalAction.ActionType.CHANGES_REQUESTED,
            comment="Private client text is never rendered here.",
            actor_role=WorkspaceMembership.WorkspaceRole.CLIENT,
            note_visibility="external",
        )

        url = reverse("calendar:calendar", args=[self.workspace.pk])
        self.client.force_login(self.owner)
        owner_response = self.client.get(url, {"mode": "calendar"})
        self.assertContains(owner_response, "Team review item")
        self.assertContains(owner_response, "Client change item")
        self.assertNotContains(owner_response, "Foreign review item must stay hidden")
        self.assertNotContains(owner_response, "Client approval item")
        self.assertNotContains(owner_response, "Private client text")

        self.client.force_login(self.client_user)
        client_response = self.client.get(url, {"mode": "calendar"})
        self.assertContains(client_response, "Client approval item")
        self.assertNotContains(client_response, "Team review item")
        self.assertNotContains(client_response, "Client change item")
        self.assertNotContains(client_response, "Disabled channel approval item")
        self.assertNotContains(client_response, "Foreign review item must stay hidden")

    def test_calendar_stage_queries_eager_load_production_for_list_and_chips(self):
        for index in range(3):
            self._content(f"Draft pipeline item {index}", PlatformPost.Status.DRAFT)
        request = RequestFactory().get("/", {"mode": "calendar"})

        posts = list(_get_filtered_posts(self.workspace, request))
        self.assertEqual(len(posts), 3)
        self.assertTrue(all("jrc_production" in post._state.fields_cache for post in posts))

        platform_posts = list(_get_filtered_platform_posts(self.workspace, request))
        self.assertEqual(len(platform_posts), 3)
        self.assertTrue(all("jrc_production" in pp.post._state.fields_cache for pp in platform_posts))
