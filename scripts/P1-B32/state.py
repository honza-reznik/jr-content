"""Stav vymyšlených obsahů P1-B32 po běhu prohlížeče (jen čtení)."""
import json
import os

from apps.composer.models import PlatformPost

seed = json.loads(os.environ['SEED_JSON'])
state = {}
for mode, ws in seed['workspaces'].items():
    for name, item in ws['posts'].items():
        state[f'{mode} {name}'] = PlatformPost.objects.get(pk=item['platform_post']).status
    csv = PlatformPost.objects.filter(post__workspace_id=ws['id'], post__caption__startswith='P1-B32 CSV').values_list('status', flat=True)
    state[f'{mode} csv'] = sorted(csv)
print('STATE=' + json.dumps(state))
