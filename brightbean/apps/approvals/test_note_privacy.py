"""Regression coverage for note text, attachments and role downgrades."""

from unittest.mock import patch

import pytest
from django.core import mail
from django.core.files.base import ContentFile
from django.test import override_settings
from django.urls import reverse

from apps.approvals import comments, services
from apps.approvals.models import PostComment
from apps.approvals.test_workflow import ApprovalWorkflowBase
from apps.members.models import WorkspaceMembership
from apps.notifications import engine
from apps.notifications.models import Channel, EventType, Notification, NotificationDelivery
from apps.notifications.privacy import GENERIC_NOTE_BODIES

SECRET = "SECRET INTERNAL NOTE"


class NoteNotificationPrivacyTests(ApprovalWorkflowBase):
    def test_client_and_viewer_authors_receive_no_internal_quote(self):
        for role in ("client", "viewer"):
            WorkspaceMembership.objects.filter(user=self.author, workspace=self.ws).update(workspace_role=role)
            for service, event in (
                (services.request_changes, EventType.POST_CHANGES_REQUESTED),
                (services.reject_post, EventType.POST_REJECTED),
            ):
                with self.subTest(role=role, event=event):
                    post = self._post("pending_review")
                    mail.outbox.clear()
                    service(post, self.reviewer, self.ws, SECRET)
                    notification = Notification.objects.get(user=self.author, data__post_id=str(post.pk))
                    self.assertEqual(notification.body, GENERIC_NOTE_BODIES[event])
                    self.assertEqual(notification.data["note_visibility"], "internal")
                    self.assertEqual(post.approval_actions.get().comment, SECRET)
                    self.assertTrue(mail.outbox)
                    self.assertNotIn(SECRET, mail.outbox[-1].message().as_string())

    def test_legacy_quotes_hidden_after_recipient_role_change_at_read_and_delivery(self):
        for event in GENERIC_NOTE_BODIES:
            notification = Notification.objects.create(
                user=self.author,
                event_type=event,
                title="Update on your post",
                body=SECRET,
                data={"workspace_id": str(self.ws.pk)},
            )
            for role in ("client", "viewer"):
                with self.subTest(event=event, role=role):
                    WorkspaceMembership.objects.filter(user=self.author, workspace=self.ws).update(workspace_role=role)
                    self.client.force_login(self.author)
                    for name, headers in (("list", {}), ("list", {"HTTP_HX_REQUEST": "true"}), ("drawer", {})):
                        response = self.client.get(reverse(f"notifications:{name}"), **headers)
                        self.assertEqual(response.status_code, 200)
                        self.assertNotContains(response, SECRET)
                        self.assertContains(response, GENERIC_NOTE_BODIES[event])
                    delivery = NotificationDelivery.objects.create(notification=notification, channel=Channel.EMAIL)
                    mail.outbox.clear()
                    engine._dispatch(delivery)
                    delivery.refresh_from_db()
                    self.assertEqual(delivery.status, "delivered")
                    self.assertEqual(len(mail.outbox), 1)
                    self.assertNotIn(SECRET, mail.outbox[0].message().as_string())
                    notification.refresh_from_db()
                    mail.outbox.clear()
                    engine._send_digest_email(self.author, None, [notification])
                    self.assertNotIn(SECRET, mail.outbox[0].message().as_string())
                    notification.refresh_from_db()
                    notification.data["webhook_url"] = "https://webhook.example.test"
                    delivery.notification = notification
                    with patch("apps.common.validators.is_safe_url", return_value=True), patch("httpx.post") as send:
                        send.return_value.status_code = 200
                        engine._dispatch_webhook(delivery)
                    self.assertNotIn(SECRET.encode(), send.call_args.kwargs["content"])
            notification.refresh_from_db()
            self.assertEqual(notification.body, SECRET)  # Presentation must not overwrite history.
            notification.delete()

    def test_recipient_access_and_stored_audience_control_quote(self):
        post = self._post("pending_review")
        services.request_changes(post, self.reviewer, self.ws, SECRET)
        notification = Notification.objects.get(user=self.author, data__post_id=str(post.pk))
        self.assertIn(SECRET, notification.body)
        # Changing the note author's role must not make an internal note external.
        WorkspaceMembership.objects.filter(user=self.reviewer, workspace=self.ws).update(workspace_role="client")
        WorkspaceMembership.objects.filter(user=self.author, workspace=self.ws).update(workspace_role="client")
        self.client.force_login(self.author)
        self.assertNotContains(self.client.get(reverse("notifications:drawer")), SECRET)
        # An explicitly external note stays readable for a current member.
        notification.data["note_visibility"] = "external"
        notification.save(update_fields=["data"])
        self.assertContains(self.client.get(reverse("notifications:drawer")), SECRET)
        WorkspaceMembership.objects.filter(user=self.author, workspace=self.ws).delete()
        self.assertNotContains(self.client.get(reverse("notifications:drawer")), SECRET)


class CommentPrivacyTests(ApprovalWorkflowBase):
    def test_client_and_viewer_attachment_visibility_including_parent_thread(self):
        post = self._post("draft")
        with override_settings(STORAGES={"default": {"BACKEND": "django.core.files.storage.InMemoryStorage"}}):
            for role in ("client", "viewer"):
                WorkspaceMembership.objects.filter(user=self.author, workspace=self.ws).update(workspace_role=role)
                self.client.force_login(self.author)
                for parent_visibility, visibility, expected in (
                    (None, "internal", 404),
                    (None, "external", 200),
                    ("external", "internal", 404),
                    ("internal", "external", 404),
                    ("external", "external", 200),
                ):
                    with self.subTest(role=role, parent=parent_visibility, visibility=visibility):
                        parent = None
                        if parent_visibility:
                            parent = PostComment.objects.create(
                                post=post, author=self.reviewer, body="Thread", visibility=parent_visibility
                            )
                        comment = PostComment.objects.create(
                            post=post,
                            author=self.reviewer,
                            body=SECRET,
                            visibility=visibility,
                            parent_comment=parent,
                            attachment=ContentFile(b"private-image", name="note.png"),
                        )
                        url = reverse("approvals:comment_attachment", args=[self.ws.pk, post.pk, comment.pk])
                        with patch.object(
                            comment.attachment.storage, "open", wraps=comment.attachment.storage.open
                        ) as opened:
                            response = self.client.get(url)
                        self.assertEqual(response.status_code, expected)
                        if expected == 404:
                            opened.assert_not_called()
                        else:
                            self.assertEqual(b"".join(response.streaming_content), b"private-image")
                            self.assertIn("no-store", response["Cache-Control"])
                        if parent:
                            parent.deleted_at = post.created_at
                            parent.save(update_fields=["deleted_at"])
                            self.assertEqual(self.client.get(url).status_code, 404)

    def test_role_downgrade_blocks_attachment_and_edit_of_own_internal_comment(self):
        post = self._post("draft")
        comment = comments.create_comment(post, self.author, SECRET)
        # No storage access is necessary: authorization runs before file opening.
        comment.attachment = "comment_attachments/private.png"
        comment.save(update_fields=["attachment"])
        for role in ("client", "viewer"):
            with self.subTest(role=role):
                WorkspaceMembership.objects.filter(user=self.author, workspace=self.ws).update(workspace_role=role)
                self.client.force_login(self.author)
                self.assertEqual(
                    self.client.get(
                        reverse("approvals:comment_attachment", args=[self.ws.pk, post.pk, comment.pk])
                    ).status_code,
                    404,
                )
                self.assertEqual(
                    self.client.post(
                        reverse("approvals:edit_comment", args=[self.ws.pk, post.pk, comment.pk]), {"body": "Rewritten"}
                    ).status_code,
                    404,
                )
                with pytest.raises(PermissionError):
                    comments.update_comment(comment.pk, self.author, "Rewritten", workspace=self.ws)
                comment.refresh_from_db()
                self.assertEqual(comment.body, SECRET)
                # Nor may an external reply be edited through an internal thread.
                reply = PostComment.objects.create(
                    post=post, author=self.author, body="Reply", parent_comment=comment, visibility="external"
                )
                with pytest.raises(PermissionError):
                    comments.update_comment(reply.pk, self.author, "Rewritten", workspace=self.ws)
                external = comments.create_comment(post, self.author, "Shared")
                response = self.client.post(
                    reverse("approvals:edit_comment", args=[self.ws.pk, post.pk, external.pk]),
                    {"body": "Updated shared"},
                )
                self.assertEqual(response.status_code, 200)
                external.refresh_from_db()
                self.assertEqual(external.body, "Updated shared")
