"""P2-B05 R1 live check: v2 becomes the approved final, v1 stays a working version (synthetic data only)."""

import json
import os

from apps.composer.models import PlatformPost
from apps.jrc_video.models import VideoAsset

assert os.environ.get("JRC_PROJECT") == "jrc-p2b05"
fixture = json.loads(input())
video = VideoAsset.objects.get(platform_post__post_id=fixture["post"], versions__number=2)
video.final_version = video.versions.get(number=2, status="ready")
video.save(update_fields=["final_version"])
variant = video.platform_post
variant.status = PlatformPost.Status.APPROVED
variant.platform_specific_caption = "Synthetic P2-B05 caption"
variant.save(update_fields=["status", "platform_specific_caption"])
print(json.dumps({"variant": str(variant.pk), "final": video.final_version.number}))
