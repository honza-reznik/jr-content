"""Stav vymyšlených dat P1-B37 po běhu prohlížeče (jen jrc-p1b37). Vstup: SEED JSON v proměnné B37_SEED."""
import json
import os

from apps.calendar.models import QueueEntry
from apps.composer.models import PlatformPost, Post
from apps.jrc_video.models import VideoAsset

seed = json.loads(os.environ['B37_SEED'])
state = {}
for name in ('planned', 'client_wait', 'composer', 'thumb', 'video'):
    item = seed[name]
    post = Post.objects.get(pk=item['post'])
    copies = {key: PlatformPost.objects.get(pk=pk) for key, pk in item.items() if key != 'post'}
    state[name] = {
        'copies': {key: [pp.status, str(pp.scheduled_at)] for key, pp in copies.items()},
        'post_scheduled_at': str(post.scheduled_at),
        'proposed_publish_at': str(post.proposed_publish_at),
        'queue_entries': QueueEntry.objects.filter(post=post).count(),
        'caption': copies['ig'].platform_specific_caption,
    }
video = VideoAsset.objects.filter(platform_post_id=seed['video']['ig']).select_related('final_version').first()
state['video']['final'] = video.final_version.number if video and video.final_version_id else None
state['video']['versions'] = sorted(video.versions.values_list('number', 'status')) if video else []
print('STATE=' + json.dumps(state, ensure_ascii=False))
