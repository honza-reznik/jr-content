"""Visibility regressions across every comment display and deletion boundary."""

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.approvals import comments
from apps.approvals.models import ApprovalAction, PostComment
from apps.approvals.test_workflow import ApprovalWorkflowBase
from apps.composer.models import Post
from apps.members.models import CustomRole, OrgMembership, WorkspaceMembership
from apps.workspaces.models import Workspace


def _flatten(roots):
    nodes = list(roots)
    for node in nodes:
        nodes.extend(node.visible_replies)
    return nodes


class CommentDisplayPrivacyTests(ApprovalWorkflowBase):
    def setUp(self):
        super().setUp()
        OrgMembership.objects.create(user=self.author, organization=self.org, org_role="member")
        self.post = self._post("pending_client")
        self.shared = self._comment("Shared root", "external")
        self.reply = self._comment("Shared reply", "external", self.shared)
        self._comment("Shared nested reply", "external", self.reply)
        self.internal = self._comment("SECRET internal root", "internal")
        self._comment("SECRET external under internal", "external", self.internal)
        secret_reply = self._comment("SECRET internal reply", "internal", self.shared)
        self._comment("SECRET nested below internal reply", "external", secret_reply)
        self._comment("SECRET nested internal reply", "internal", self.reply)
        deleted = self._comment("SECRET deleted root", "external")
        deleted.deleted_at = timezone.now()
        deleted.save(update_fields=["deleted_at"])
        self._comment("SECRET under deleted root", "external", deleted)
        deleted_reply = self._comment("SECRET deleted reply", "external", self.shared)
        deleted_reply.deleted_at = timezone.now()
        deleted_reply.save(update_fields=["deleted_at"])
        self._comment("SECRET under deleted reply", "external", deleted_reply)

    def _comment(self, body, visibility, parent=None, post=None):
        return PostComment.objects.create(
            post=post or self.post, author=self.author, body=body, visibility=visibility, parent_comment=parent
        )

    def _login(self, role):
        WorkspaceMembership.objects.filter(user=self.author, workspace=self.ws).update(workspace_role=role)
        self.client.force_login(self.author)

    def _assert_external_tree(self, roots):
        self.assertEqual(
            {node.body for node in _flatten(roots)}, {"Shared root", "Shared reply", "Shared nested reply"}
        )

    def test_client_and_viewer_see_only_visible_tree_in_each_display(self):
        for role in ("client", "viewer"):
            for surface in ("detail", "calendar", "portal", "add", "edit", "delete"):
                with self.subTest(role=role, surface=surface):
                    self._login(role)
                    if surface == "detail":
                        response = self.client.get(
                            reverse("jrc_production:content_detail", args=[self.ws.pk, self.post.pk]),
                            {"tab": "comments"},
                        )
                        roots = response.context["comments"]
                    elif surface == "calendar":
                        response = self.client.get(reverse("calendar:publish_tab_approvals", args=[self.ws.pk]))
                        roots = response.context["posts"][0].visible_comments
                    elif surface == "portal":
                        session = self.client.session
                        session["is_portal_session"] = True
                        session["portal_workspace_id"] = str(self.ws.pk)
                        session.save()
                        response = self.client.get(reverse("client_portal:approval_queue"))
                        roots = response.context["pending_posts"][0].visible_comments
                    else:
                        temporary = self._comment("Temporary shared comment", "external")
                        args = [self.ws.pk, self.post.pk]
                        if surface != "add":
                            args.append(temporary.pk)
                        response = self.client.post(
                            reverse(f"approvals:{surface}_comment", args=args), {"body": "Updated shared comment"}
                        )
                        roots = [root for root in response.context["comments"] if root.pk == self.shared.pk]
                        temporary.delete()
                        PostComment.objects.filter(body="Updated shared comment").delete()
                    self.assertEqual(response.status_code, 200)
                    self.assertNotContains(response, "SECRET")
                    for body in ("Shared root", "Shared reply", "Shared nested reply"):
                        self.assertContains(response, body)
                    self._assert_external_tree(roots)

    def test_composer_denies_client_and_viewer_and_shows_team_tree_to_manager(self):
        ApprovalAction.objects.create(post=self.post, user=self.reviewer, action="submitted")
        url = reverse("composer:compose_edit", args=[self.ws.pk, self.post.pk])
        for role in ("client", "viewer"):
            self._login(role)
            response = self.client.get(url)
            self.assertEqual(response.status_code, 403)
            self.assertNotContains(response, "SECRET", status_code=403)
        self.client.force_login(self.reviewer)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "SECRET internal reply")
        self.assertContains(response, "Shared nested reply")
        self.assertNotContains(response, "SECRET under deleted reply")

    def test_batch_selector_checks_ancestors_scope_cycles_and_current_membership(self):
        other_post = self._post("pending_client")
        self._comment("SECRET cross-post child", "external", self.shared, other_post)
        cycle = self._comment("SECRET cycle", "external")
        child = self._comment("SECRET cycle child", "external", cycle)
        PostComment.objects.filter(pk=cycle.pk).update(parent_comment=child)
        for role in ("client", "viewer"):
            self._login(role)
            # One membership lookup and one batch of all comments, at any depth.
            with self.assertNumQueries(2):
                roots = comments.get_visible_comments([self.post, other_post], self.author, self.ws)
                self._assert_external_tree(roots)
        roots = comments.get_comments_for_post(self.post, self.reviewer)
        self.assertIn("SECRET nested below internal reply", {node.body for node in _flatten(roots)})
        self.author.is_active = False
        self.assertEqual(comments.get_comments_for_post(self.post, self.author), [])
        self.author.is_active = True
        self.ws.is_archived = True
        self.assertEqual(comments.get_comments_for_post(self.post, self.author), [])
        self.ws.is_archived = False
        WorkspaceMembership.objects.filter(user=self.author, workspace=self.ws).delete()
        self.assertEqual(comments.get_comments_for_post(self.post, self.author), [])

    def test_custom_permissions_do_not_expose_internal_comments_in_composer(self):
        ApprovalAction.objects.create(post=self.post, user=self.reviewer, action="submitted")
        custom_role = CustomRole.objects.create(
            organization=self.org, name="Composer access", permissions={"create_posts": True}
        )
        WorkspaceMembership.objects.filter(user=self.author, workspace=self.ws).update(custom_role=custom_role)
        for role in ("client", "viewer"):
            with self.subTest(role=role):
                self._login(role)
                response = self.client.get(reverse("composer:compose_edit", args=[self.ws.pk, self.post.pk]))
                self.assertEqual(response.status_code, 200)
                self.assertNotContains(response, "SECRET")
                self.assertContains(response, "Shared nested reply")
                self._assert_external_tree(response.context["post_comments"])


class DeleteCommentPrivacyTests(ApprovalWorkflowBase):
    def _delete_url(self, comment, post=None, workspace=None):
        return reverse(
            "approvals:delete_comment", args=[(workspace or self.ws).pk, (post or comment.post).pk, comment.pk]
        )

    def test_wrong_post_or_workspace_cannot_delete_even_as_author_or_moderator(self):
        local = self._post("draft")
        other = self._post("draft")
        foreign_ws = Workspace.objects.create(organization=self.org, name="Other workspace")
        foreign_post = Post.objects.create(workspace=foreign_ws, author=self.author, caption="Other")
        for user in (self.author, self.reviewer):
            self.client.force_login(user)
            for comment_post, url_post, url_workspace in (
                (other, local, self.ws),
                (foreign_post, local, self.ws),
                (foreign_post, foreign_post, self.ws),
            ):
                with self.subTest(user=user.email, post=comment_post.pk, url_post=url_post.pk):
                    comment = PostComment.objects.create(
                        post=comment_post, author=self.author, body="Untouched", visibility="external"
                    )
                    self.assertEqual(
                        self.client.post(self._delete_url(comment, url_post, url_workspace)).status_code, 404
                    )
                    with pytest.raises(ValueError):
                        comments.delete_comment(comment.pk, user, self.ws, post=url_post)
                    comment.refresh_from_db()
                    self.assertIsNone(comment.deleted_at)

    def test_client_and_viewer_cannot_delete_own_hidden_comments_or_replies(self):
        post = self._post("draft")
        internal = PostComment.objects.create(post=post, author=self.author, body="Internal")
        reply = PostComment.objects.create(
            post=post, author=self.author, body="Shared under internal", parent_comment=internal, visibility="external"
        )
        nested = PostComment.objects.create(
            post=post, author=self.author, body="Nested", parent_comment=reply, visibility="external"
        )
        external = PostComment.objects.create(post=post, author=self.author, body="Shared", visibility="external")
        hidden_reply = PostComment.objects.create(
            post=post, author=self.author, body="Internal reply", parent_comment=external
        )
        for role in ("client", "viewer"):
            WorkspaceMembership.objects.filter(user=self.author, workspace=self.ws).update(workspace_role=role)
            self.client.force_login(self.author)
            for comment in (internal, reply, nested, hidden_reply):
                with self.subTest(role=role, comment=comment.body):
                    self.assertEqual(self.client.post(self._delete_url(comment)).status_code, 404)
                    with pytest.raises(PermissionError):
                        comments.delete_comment(comment.pk, self.author, self.ws, post=post)
                    comment.refresh_from_db()
                    self.assertIsNone(comment.deleted_at)
            allowed = PostComment.objects.create(post=post, author=self.author, body="Allowed", visibility="external")
            self.assertEqual(self.client.post(self._delete_url(allowed)).status_code, 200)
            allowed.refresh_from_db()
            self.assertIsNotNone(allowed.deleted_at)
        self.client.force_login(self.reviewer)
        self.assertEqual(self.client.post(self._delete_url(internal)).status_code, 200)
        internal.refresh_from_db()
        self.assertIsNotNone(internal.deleted_at)

    def test_nonmember_inactive_and_archived_cannot_use_delete_service(self):
        post = self._post("draft")
        comment = PostComment.objects.create(post=post, author=self.author, body="Own")
        self.author.is_active = False
        with pytest.raises(PermissionError):
            comments.delete_comment(comment.pk, self.author, self.ws, post=post)
        self.author.is_active = True
        self.ws.is_archived = True
        # Reloaded comment's workspace reflects persisted state.
        self.ws.save(update_fields=["is_archived"])
        with pytest.raises(PermissionError):
            comments.delete_comment(comment.pk, self.author, self.ws, post=post)
        self.ws.is_archived = False
        self.ws.save(update_fields=["is_archived"])
        WorkspaceMembership.objects.filter(user=self.author, workspace=self.ws).delete()
        with pytest.raises(PermissionError):
            comments.delete_comment(comment.pk, self.author, self.ws, post=post)
        comment.refresh_from_db()
        self.assertIsNone(comment.deleted_at)
