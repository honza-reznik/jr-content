"""Ověření výsledků v databázi Brightbean, bez přenosu přihlašovacích údajů do logu."""
import json
import os
import sys
from apps.composer.models import PlatformPost, Post
from apps.jrc_video.models import VideoCommentReceipt
from apps.notifications.models import Notification

assert os.environ.get("JRC_PROJECT") == "jrc-p1b13"
fixture = json.load(sys.stdin)
other = PlatformPost.objects.filter(pk=fixture["other"]).values().get()
assert json.dumps(other, default=str, sort_keys=True) == fixture["other_snapshot"]
target = PlatformPost.objects.get(pk=fixture["target"])
assert target.status == "scheduled" and target.scheduled_at is not None
post = Post.objects.get(pk=fixture["post"])
assert post.scheduled_at == target.scheduled_at
assert not post.media_attachments.exists()
alerts = Notification.objects.filter(event_type="content_client_comment")
assert alerts.count() == 2, alerts.count()
assert set(alerts.values_list("user__email", flat=True)) == {fixture["editor"]["email"], fixture["manager"]["email"]}
assert VideoCommentReceipt.objects.count() == 1
assert all("tab=video" in alert.data["action_url"] for alert in alerts)
print("OK: termín uložen, druhá kopie beze změny; právě 1 upozornění ownerovi a 1 manažerovi, 1 potvrzení události.")
