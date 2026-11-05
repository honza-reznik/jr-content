"""Schválení pouze vymyšlených obsahů vlastního projektu, po nahrání skutečného videa."""

import os

from apps.composer.models import PlatformPost, Post
from apps.composer.services import transition_platform_post

assert os.environ.get("JRC_PUBLIC_ORIGIN") == "http://127.0.0.1:13563"
for post in Post.objects.filter(workspace__name="Garden test", workspace__organization__name="P1-B14 local studio"):
    for variant in post.platform_posts.all():
        transition_platform_post(variant, PlatformPost.Status.PENDING_REVIEW)
        variant.refresh_from_db()
        transition_platform_post(variant, PlatformPost.Status.APPROVED)
print("Schváleny jen obsahy P1-B14.")
