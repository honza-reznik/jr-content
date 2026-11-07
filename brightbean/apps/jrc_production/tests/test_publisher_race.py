# JR Content: přidáno 2026-11-07 (blok P1-B37, kontrola), AGPL-3.0.
"""Edits, unscheduling and deletion against the publisher claiming a copy (P1-B37 review, finding 1).

Every writer of a copy's state locks the content and then its copies by primary key, exactly as the
publisher does when it claims due copies (``composer.services.lock_copies``). So:

* an edit that started first holds the copies until they are back in review; the publisher waits
  and then claims nothing (the copy is no longer ``scheduled``);
* a writer that comes while the publisher holds its claim waits, then sees ``publishing`` and
  leaves the copy alone – it is never overwritten back to review, to a draft, or deleted in bulk.

Real PostgreSQL connections in threads; expectations are written by hand.
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from queue import Queue
from threading import Event
from uuid import uuid4

import pytest
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import User
from apps.approvals.edits import return_edited_to_review, reviewed_content
from apps.calendar.models import Queue as PostingQueue
from apps.calendar.models import QueueEntry
from apps.composer.models import PlatformPost, Post
from apps.composer.services import transition_platform_post
from apps.jrc_channels.services import create_manual_channel
from apps.jrc_production.tests.test_composer_captions import (  # noqa: F401 (concurrent_database is a fixture)
    _thread_request,
    _wait_for_post_lock,
    concurrent_database,
)
from apps.members.models import OrgMembership, WorkspaceMembership
from apps.publisher.engine import PublishEngine
from apps.workspaces.models import Workspace


@pytest.fixture
def planned(organization):
    """Due planned content in a mode that requires approval, its owner, and a queue entry."""
    workspace = Workspace.objects.create(
        organization=organization, name=f"B37 race {uuid4().hex[:6]}", approval_workflow_mode="required_internal"
    )
    account = create_manual_channel(workspace, "custom", "B37 race channel")
    user = User.objects.create_user(email=f"b37-race-{uuid4().hex[:8]}@example.invalid", tos_accepted_at=timezone.now())
    OrgMembership.objects.create(user=user, organization=organization, org_role="owner")
    WorkspaceMembership.objects.create(user=user, workspace=workspace, workspace_role="owner")
    when = timezone.now() - timedelta(minutes=1)
    post = Post.objects.create(
        workspace=workspace, author=user, title="B37 race", caption="Approved", scheduled_at=when
    )
    copy = PlatformPost.objects.create(post=post, social_account=account, status="scheduled", scheduled_at=when)
    queue = PostingQueue.objects.create(workspace=workspace, social_account=account, name="B37 race queue")
    entry = QueueEntry.objects.create(queue=queue, post=post, position=0, assigned_slot_datetime=when)
    return workspace, post, copy, user, queue, entry


@pytest.mark.django_db(transaction=True)
@pytest.mark.usefixtures("concurrent_database")
def test_edit_first_the_publisher_waits_and_then_claims_nothing(planned):
    workspace, post, copy, user, queue, entry = planned
    snapshot_taken, release = Event(), Event()

    def edit(_http):
        with transaction.atomic():
            content = Post.objects.select_related("workspace").get(pk=post.pk)
            before = reviewed_content(content)
            content.caption = "Edited while it was due"
            content.save(update_fields=["caption"])
            snapshot_taken.set()
            assert release.wait(8)
            return [pp.pk for pp in return_edited_to_review(content, before)]

    def claim(_http):
        due = list(PlatformPost.objects.filter(pk=copy.pk))
        return PublishEngine()._publish_post_group(Post.objects.get(pk=post.pk), due)

    pids = Queue()
    with ThreadPoolExecutor(max_workers=2) as pool:
        editing = pool.submit(_thread_request, user, edit)
        assert snapshot_taken.wait(8)
        publisher = pool.submit(_thread_request, user, claim, pids)
        blocked = _wait_for_post_lock(pids.get(timeout=8), publisher)
        release.set()
        assert editing.result(timeout=15) == [copy.pk]
        assert publisher.result(timeout=15) is False
    assert blocked
    copy.refresh_from_db()
    assert (copy.status, copy.scheduled_at) == ("pending_review", None)


def _caption(http, workspace, post, copy, queue, entry):
    return http.post(
        f"/workspace/{workspace.pk}/content/{post.pk}/?tab=caption",
        {"action": "save_caption", "platform_post": str(copy.pk), "caption": "Edited during publishing"},
    ).status_code


def _composer(http, workspace, post, copy, queue, entry):
    return http.post(
        f"/workspace/{workspace.pk}/compose/{post.pk}/save/",
        {
            "action": "save_draft",
            "title": post.title,
            "caption": "Edited",
            "selected_accounts": str(copy.social_account_id),
        },
        HTTP_HX_REQUEST="true",
    ).status_code


def _bulk(action):
    def run(http, workspace, post, copy, queue, entry):
        response = http.post(
            f"/workspace/{workspace.pk}/calendar/bulk-action/", {"action": action, "platform_post_ids": [str(copy.pk)]}
        )
        return response.status_code, response.json().get("count")

    return run


def _chip_draft(http, workspace, post, copy, queue, entry):
    return http.post(
        f"/workspace/{workspace.pk}/compose/{post.pk}/platform-posts/{copy.pk}/transition/", {"target_status": "draft"}
    ).status_code


def _queue_remove(http, workspace, post, copy, queue, entry):
    return http.post(f"/workspace/{workspace.pk}/calendar/queues/{queue.pk}/entries/{entry.pk}/remove/").status_code


def _service_cancel(http, workspace, post, copy, queue, entry):
    """REST ``/cancel``, MCP ``cancel_post`` and the queue go through this helper with a copy read earlier."""
    stale = PlatformPost.objects.get(pk=copy.pk)
    stale.status = "scheduled"
    try:
        transition_platform_post(stale, "draft")
    except ValueError:
        return "refused"
    return "moved"


WRITERS = {
    "caption": (_caption, 302),
    "composer": (_composer, 400),  # the composer is read-only for content being published
    "bulk_draft": (_bulk("draft"), (200, 0)),
    "bulk_delete": (_bulk("delete"), (200, 0)),
    "chip_draft": (_chip_draft, 400),
    "queue_remove": (_queue_remove, 200),
    "service_cancel": (_service_cancel, "refused"),
}


@pytest.mark.django_db(transaction=True)
@pytest.mark.usefixtures("concurrent_database")
@pytest.mark.parametrize("writer", list(WRITERS))
def test_writer_during_the_claim_waits_and_leaves_the_publishing_copy_alone(planned, writer):
    workspace, post, copy, user, queue, entry = planned
    operation, expected = WRITERS[writer]
    claimed, release = Event(), Event()

    def hold_claim(_http):
        # The publisher's claim: content, then its copies by primary key, then scheduled -> publishing.
        with transaction.atomic():
            list(Post.objects.select_for_update(of=("self",)).filter(pk=post.pk).values_list("pk"))
            list(PlatformPost.objects.select_for_update(of=("self",)).filter(post_id=post.pk).order_by("pk"))
            PlatformPost.objects.filter(pk=copy.pk).update(status="publishing")
            claimed.set()
            assert release.wait(8)

    pids = Queue()
    with ThreadPoolExecutor(max_workers=2) as pool:
        holding = pool.submit(_thread_request, user, hold_claim)
        assert claimed.wait(8)
        writing = pool.submit(
            _thread_request, user, lambda http: operation(http, workspace, post, copy, queue, entry), pids
        )
        blocked = _wait_for_post_lock(pids.get(timeout=8), writing)
        release.set()
        holding.result(timeout=15)
        result = writing.result(timeout=15)
    assert blocked
    assert result == expected
    copy.refresh_from_db()
    assert copy.status == "publishing"


@pytest.mark.django_db
def test_a_copy_claimed_after_the_snapshot_is_not_returned(planned):
    """Even a writer that skipped the locks never overwrites a copy claimed after its snapshot."""
    workspace, post, copy, user, queue, entry = planned
    with transaction.atomic():
        before = reviewed_content(post)
        PlatformPost.objects.filter(pk=copy.pk).update(status="publishing")
        post.caption = "Changed"
        post.save(update_fields=["caption"])
        assert return_edited_to_review(post, before) == []
    copy.refresh_from_db()
    assert copy.status == "publishing"
