"""DB confirmation of browser cases; cookie values never enter output."""
import json
import sys

from apps.approvals.models import ApprovalAction, PostComment
from apps.composer.models import PlatformPost, Post

seed = json.load(sys.stdin)
ids = seed['review']
for name, title, state in [('foreign', 'Review foreign', 'draft'), ('approved', 'Changed', 'pending_review'), ('published', 'Review published', 'published'), ('team_review', 'Review team_review', 'pending_review'), ('client_review', 'Review client_review', 'approved')]:
    post = Post.objects.get(pk=ids[name], workspace_id=seed['workspace'])
    assert post.title == title, (name, post.title)
    assert post.platform_posts.count() == 1
    assert post.platform_posts.get().status == state
for name, state in [('team_only_review', 'pending_review'), ('team_only_client', 'pending_client')]:
    post = Post.objects.get(pk=ids[name])
    assert post.platform_posts.get().status == state, (name, post.platform_posts.get().status)
    assert ApprovalAction.objects.filter(post=post).count() == 0
comment = PostComment.objects.get(pk=ids['comment'])
assert comment.body == 'Original team comment' and comment.deleted_at is None
assert PostComment.objects.filter(post_id=ids['team_review']).count() == 1
assert ApprovalAction.objects.filter(post_id=ids['team_review']).count() == 0
print('PASS: denied writes preserved content/channels/comments; changed approved content returned to review; published content stayed unchanged; valid client approval reached approved.')
