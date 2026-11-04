"""Příprava schváleného obsahu se dvěma verzemi; jen vlastní testovací projekt."""
import json
import os
import sys

from apps.composer.models import PlatformPost, Post
from apps.composer.services import transition_platform_post

assert os.environ.get("JRC_PROJECT") == "jrc-p1b13"
fixture = json.load(sys.stdin)
post = Post.objects.get(pk=fixture["post"])
for variant in post.platform_posts.all():
    variant.platform_specific_caption = "P1-B13 saved caption"
    variant.save(update_fields=["platform_specific_caption"])
    transition_platform_post(variant, PlatformPost.Status.PENDING_REVIEW)
    variant.refresh_from_db()
    transition_platform_post(variant, PlatformPost.Status.APPROVED)
print("P1-B13: kanály schváleny pro ověření publikace.")
