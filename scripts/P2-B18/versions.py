"""Schválená finální v2 a pracovní v1, pouze ve vymyšleném projektu P2-B18."""
import json
import os
from apps.composer.models import PlatformPost
from apps.jrc_video.models import VideoAsset
assert os.environ['JRC_PROJECT'] == 'jrc-p2b18'
f = json.loads(input())
video = VideoAsset.objects.get(platform_post__post_id=f['post'], versions__number=2)
video.final_version = video.versions.get(number=2, status='ready')
video.save(update_fields=['final_version'])
variant = video.platform_post
variant.status = PlatformPost.Status.APPROVED
variant.platform_specific_caption = 'Approved synthetic caption'
variant.save(update_fields=['status', 'platform_specific_caption'])
print(json.dumps({'asset': str(video.ff_asset_id), 'variant': str(variant.pk), 'versions': [{'bb': str(v.pk), 'ff': str(v.ff_version_id), 'number': v.number} for v in video.versions.order_by('number')]}))
