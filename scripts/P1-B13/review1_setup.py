"""Fixture review 1: dvě kopie, pouze video FreeFrame, bez PostMedia."""
import json
import os
import sys
import uuid
from datetime import UTC, datetime

from apps.composer.models import PlatformPost, Post
from apps.jrc_video.models import VideoAsset, VideoVersion, VideoAccount

assert os.environ.get("JRC_PROJECT") == "jrc-p1b13"
fixture = json.load(sys.stdin)
post = Post.objects.get(pk=fixture["post"])
post.workspace.timezone = "Europe/Prague"
post.workspace.save(update_fields=["timezone"])
variants = list(post.platform_posts.order_by("id"))
assert len(variants) == 2 and not post.media_attachments.exists()
for variant in variants:
    variant.status = PlatformPost.Status.APPROVED
    variant.platform_specific_caption = f"Saved caption {variant.pk}"
    variant.save(update_fields=["status", "platform_specific_caption"])
other = variants[1]
other.status = "scheduled"
other.scheduled_at = datetime(2098, 1, 1, tzinfo=UTC)
other.save(update_fields=["status", "scheduled_at"])
video = VideoAsset.objects.create(platform_post=variants[0], ff_asset_id=uuid.uuid4())
version = VideoVersion.objects.create(video=video, number=1, ff_version_id=uuid.uuid4(), status="ready")
video.final_version = version
video.save(update_fields=["final_version"])
fixture.update({
    "target": str(variants[0].pk), "other": str(other.pk),
    "other_snapshot": json.dumps(PlatformPost.objects.filter(pk=other.pk).values().get(), default=str, sort_keys=True),
    "asset": str(video.ff_asset_id), "version": str(version.ff_version_id),
    "ff_client": str(VideoAccount.objects.get(user__email=fixture["client"]["email"]).ff_user_id),
})
print(json.dumps(fixture))
