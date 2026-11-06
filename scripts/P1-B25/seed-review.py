"""Additional local-only cases from the review; no real accounts."""
import json

from apps.approvals.models import PostComment
from apps.composer.models import PlatformPost, Post
from apps.members.models import WorkspaceMembership
from apps.workspaces.models import Workspace

ws = Workspace.objects.get(name='P1-B25 Studio')
ws.approval_workflow_mode = 'required_internal_and_client'
ws.save()
owner = WorkspaceMembership.objects.get(workspace=ws, workspace_role='owner').user
contributor = WorkspaceMembership.objects.get(workspace=ws, workspace_role='contributor').user
account = ws.social_accounts.first()
result = {}
for name, author, status in [('foreign', owner, 'draft'), ('approved', contributor, 'approved'), ('published', contributor, 'published'), ('team_review', owner, 'pending_review'), ('client_review', owner, 'pending_client')]:
    post = Post.objects.create(workspace=ws, author=author, title='Review '+name, caption='Original')
    pp = PlatformPost.objects.create(post=post, social_account=account, status=status)
    result[name] = str(post.pk)
result['comment'] = str(PostComment.objects.create(post_id=result['team_review'], author=owner, body='Original team comment', visibility='external').pk)
# E-08-ex6b-01: a team-only workspace where the same client must never decide.
client_user = WorkspaceMembership.objects.get(workspace=ws, workspace_role='client').user
team_only, _ = Workspace.objects.get_or_create(
    organization=ws.organization, name='P1-B25 Team only', defaults={'approval_workflow_mode': 'required_internal'}
)
WorkspaceMembership.objects.get_or_create(workspace=team_only, user=owner, defaults={'workspace_role': 'owner'})
WorkspaceMembership.objects.get_or_create(workspace=team_only, user=client_user, defaults={'workspace_role': 'client'})
team_account = account.__class__.objects.create(workspace=team_only, platform='instagram', account_name='P1-B25 team only')
result['team_only_workspace'] = str(team_only.pk)
for name, status in [('team_only_review', 'pending_review'), ('team_only_client', 'pending_client')]:
    post = Post.objects.create(workspace=team_only, author=owner, title='Review '+name, caption='Original')
    PlatformPost.objects.create(post=post, social_account=team_account, status=status)
    result[name] = str(post.pk)
print('REVIEW='+json.dumps(result))
