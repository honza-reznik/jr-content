"""Ověření, že odmítnuté živé požadavky nezměnily obsah ani kanály."""
import json
import os
import sys

from apps.composer.models import PlatformPost, Post

assert os.environ['JRC_PUBLIC_ORIGIN'] == 'http://127.0.0.1:13673'
seed = json.load(sys.stdin)
for role, actions in seed['posts'].items():
    for action, post_id in actions.items():
        post = Post.objects.get(pk=post_id, workspace_id=seed['workspace'])
        assert post.title == f'P1-B25 {role} {action}'
        assert PlatformPost.objects.filter(post=post, social_account_id=seed['account'], status='draft').count() == 1
print('PASS: všechny 4 obsahy a jejich 4 kanály zachovány beze změny.')
