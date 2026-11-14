"""Okrajové stavy pro živý průchod, pouze na vymyšleném obsahu P2-B18."""
import json
import os
import sys
from apps.composer.models import Post, PlatformPost
from apps.jrc_video.models import VideoAsset, VideoVersion
assert os.environ['JRC_PROJECT'] == 'jrc-p2b18'
f = json.loads(input())
video = VideoAsset.objects.get(platform_post__post_id=f['post'], versions__number=2)
if sys.argv[1] in ['pending_client', 'approved']:
    PlatformPost.objects.filter(pk=video.platform_post_id).update(status=sys.argv[1])
elif sys.argv[1] == 'long':
    Post.objects.filter(pk=f['post']).update(title='A long synthetic title for testing readable video download controls ' * 3)
    VideoVersion.objects.filter(video=video).update(original_filename='VeryLong' + 'Clip' * 50 + '.MOV')
print('OK změněn vlastní ověřovací stav')
