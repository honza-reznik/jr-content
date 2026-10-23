"""Comment mention notifications must respect comment visibility."""

from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import User
from apps.approvals.comments import create_comment
from apps.approvals.models import PostComment
from apps.composer.models import Post
from apps.members.models import WorkspaceMembership
from apps.notifications.models import EventType, Notification
from apps.organizations.models import Organization
from apps.workspaces.models import Workspace


class CommentMentionVisibilityTests(TestCase):
    def setUp(self):
        self.organization = Organization.objects.create(name="Mention test org")
        self.workspace = Workspace.objects.create(organization=self.organization, name="Mention test workspace")
        self.author = User.objects.create_user(
            email="author@example.com", password="testpass123", tos_accepted_at=timezone.now()
        )
        self.client_user = User.objects.create_user(
            email="client@example.com", password="testpass123", tos_accepted_at=timezone.now()
        )
        WorkspaceMembership.objects.create(
            user=self.author, workspace=self.workspace, workspace_role=WorkspaceMembership.WorkspaceRole.EDITOR
        )
        WorkspaceMembership.objects.create(
            user=self.client_user, workspace=self.workspace, workspace_role=WorkspaceMembership.WorkspaceRole.CLIENT
        )
        self.post = Post.objects.create(workspace=self.workspace, author=self.author, caption="A post")

    def test_internal_comment_does_not_notify_mentioned_client(self):
        comment = create_comment(
            self.post,
            self.author,
            "Internal note for @client: launch is delayed",
            visibility=PostComment.Visibility.INTERNAL,
        )

        self.assertEqual(comment.visibility, PostComment.Visibility.INTERNAL)
        self.assertFalse(
            Notification.objects.filter(user=self.client_user, event_type=EventType.COMMENT_MENTION).exists()
        )

    def test_external_comment_notifies_mentioned_client(self):
        create_comment(
            self.post,
            self.author,
            "Please review this update, @client",
            visibility=PostComment.Visibility.EXTERNAL,
        )

        self.assertTrue(
            Notification.objects.filter(user=self.client_user, event_type=EventType.COMMENT_MENTION).exists()
        )
