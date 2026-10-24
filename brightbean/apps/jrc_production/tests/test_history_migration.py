import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from apps.approvals.models import ApprovalAction
from apps.composer.models import Post
from apps.jrc_production.models import StageChange
from apps.members.models import WorkspaceMembership


@pytest.mark.django_db(transaction=True)
def test_old_history_migrates_to_internal_without_guessing_current_role(workspace, user):
    post = Post.objects.create(workspace=workspace, author=user, title="Legacy content")
    WorkspaceMembership.objects.filter(workspace=workspace, user=user).update(workspace_role="client")
    old = [
        ("approvals", "0004_postcomment_is_resolved_alter_postcomment_visibility"),
        ("jrc_production", "0001_initial"),
    ]
    new = [
        ("approvals", "0005_approvalaction_note_audience"),
        ("jrc_production", "0002_stagechange_note_audience"),
    ]
    executor = MigrationExecutor(connection)
    try:
        executor.migrate(old)
        apps = executor.loader.project_state(old).apps
        action = apps.get_model("approvals", "ApprovalAction").objects.create(
            post_id=post.pk, user_id=user.pk, action="approved", comment="Old team note"
        )
        change = apps.get_model("jrc_production", "StageChange").objects.create(
            post_id=post.pk, user_id=user.pk, from_stage="idea", to_stage="script", note="Old stage note"
        )
        MigrationExecutor(connection).migrate(new)
        for row in (ApprovalAction.objects.get(pk=action.pk), StageChange.objects.get(pk=change.pk)):
            assert row.note_visibility == "internal"
            assert row.actor_role == ""
    finally:
        MigrationExecutor(connection).migrate(new)
