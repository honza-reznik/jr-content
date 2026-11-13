"""Prepare publishing state only in the synthetic P2-B05 fixture."""
import json
import os
from apps.composer.models import PlatformPost
from apps.jrc_video.models import VideoAsset

assert os.environ.get("JRC_PROJECT") == "jrc-p2b05"
fixture = json.loads(input())
video = VideoAsset.objects.filter(platform_post__post_id=fixture["post"]).first()
assert video
video.final_version = video.versions.get(status="ready")
video.save(update_fields=["final_version"])
variant = video.platform_post
variant.status = PlatformPost.Status.APPROVED
variant.platform_specific_caption = "Synthetic P2-B05 caption"
variant.save(update_fields=["status", "platform_specific_caption"])
print(json.dumps({"variant": str(variant.pk), "asset": str(video.ff_asset_id), "version": str(video.final_version.ff_version_id)}))
