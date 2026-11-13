"""Visual edge-case data, only in the P2-B05 project, restored after screenshots."""
import json
import os
from pathlib import Path
from apps.composer.models import Post
from apps.jrc_video.models import VideoVersion
from apps.social_accounts.models import SocialAccount
assert os.environ['JRC_PROJECT'] == 'jrc-p2b05'
fixture = json.loads(input())
backup = Path('/tmp/p2b05-long-data.json')
post = Post.objects.get(pk=fixture['post'])
version = VideoVersion.objects.filter(video__platform_post__post=post).first()
account = version.video.platform_post.social_account
if os.environ['P2B05_LONG'] == '1':
    assert not backup.exists()
    backup.write_text(json.dumps({'title': post.title, 'filename': version.original_filename, 'channel': account.account_name}))
    Post.objects.filter(pk=post.pk).update(title='A very long synthetic title for a video review ' * 3)
    VideoVersion.objects.filter(pk=version.pk).update(original_filename='VeryLong' + 'Clip' * 50 + '.MOV')
    SocialAccount.objects.filter(pk=account.pk).update(account_name='LongChannel' * 12)
else:
    values = json.loads(backup.read_text())
    Post.objects.filter(pk=post.pk).update(title=values['title'])
    VideoVersion.objects.filter(pk=version.pk).update(original_filename=values['filename'])
    SocialAccount.objects.filter(pk=account.pk).update(account_name=values['channel'])
    backup.unlink()
print('OK synthetic long data changed')
