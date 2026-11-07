"""Stav vymyšlených obsahů P1-B34 po běhu prohlížeče (jen čtení)."""
import json
import os

from apps.composer.models import PlatformPost

seed = json.loads(os.environ['SEED_JSON'])
state = {}
for mode, ws in seed['workspaces'].items():
    for name, item in ws['posts'].items():
        pp = PlatformPost.objects.get(pk=item['platform_post'])
        state[f'{mode} {name}'] = [pp.status, pp.scheduled_at.isoformat() if pp.scheduled_at else None, pp.post.caption]
    rows = PlatformPost.objects.filter(post__workspace_id=ws['id'], post__caption__startswith='B34 CSV')
    state[f'{mode} csv'] = sorted(f'{pp.post.caption}: {pp.status}' for pp in rows)
    from apps.composer.models import Post

    bare = Post.objects.filter(workspace_id=ws['id'], caption__startswith='B34 CSV', platform_posts__isnull=True)
    state[f'{mode} csv without channel'] = sorted(p.caption for p in bare)
print('STATE=' + json.dumps(state))
