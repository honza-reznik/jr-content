"""Stav vymyšlených dat P1-B38 po běhu prohlížeče (jen jrc-p1b38). Vstup: SEED JSON v proměnné B38_SEED."""
import json
import os

from apps.composer.models import PlatformPost, Post
from apps.jrc_production.models import ContentRename

seed = json.loads(os.environ['B38_SEED'])
state = {}
for mode, ws in seed['workspaces'].items():
    state[mode] = {
        'title': Post.objects.get(pk=ws['post']).title,
        'copy_status': PlatformPost.objects.get(pk=ws['copy']).status,
        'renames': list(ContentRename.objects.filter(post_id=ws['post']).values_list('old_title', 'new_title')),
        'doomed_exists': Post.objects.filter(pk=ws['doomed']).exists(),
    }
print('STATE=' + json.dumps(state, ensure_ascii=False))
