"""Stav vymyšlených dat P1-B36 po běhu prohlížeče (jen jrc-p1b36). Vstup: SEED JSON v proměnné B36_SEED."""
import json
import os

from apps.composer.models import PlatformPost, Post

seed = json.loads(os.environ['B36_SEED'])
state = {}
for mode, ws in seed['workspaces'].items():
    copies = {name: PlatformPost.objects.get(pk=ws[name]) for name in ('edited', 'other', 'planned')}
    state[mode] = {name: [pp.status, pp.platform_specific_caption, str(pp.scheduled_at)] for name, pp in copies.items()}
    state[mode]['imported'] = sorted(Post.objects.filter(workspace_id=ws['id'], caption__startswith='B36 TSV').values_list('caption', flat=True))
print('STATE=' + json.dumps(state, ensure_ascii=False))
