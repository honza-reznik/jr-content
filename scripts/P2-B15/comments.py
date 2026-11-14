"""P2-B15: komentáře FreeFrame k v1 výstupu YouTube · Video jménem ownera a klientky (přes jejich vlastní tokeny)."""

import json
import os

from apps.accounts.models import User
from apps.jrc_video.client import FreeFrameUserClient
from apps.jrc_video.models import VideoVersion

assert os.environ.get("JRC_PROJECT") == "jrc-p2b15"
version = VideoVersion.objects.get(number=1, video__platform_post__output_format="long_form")
asset, ff_version = version.video.ff_asset_id, str(version.ff_version_id)
owner = FreeFrameUserClient(User.objects.get(email="owner-p2b15@example.invalid"))
client = FreeFrameUserClient(User.objects.get(email="ella-p2b15@example.invalid"))
public = client.request("POST", f"/assets/{asset}/comments", {
    "version_id": ff_version, "body": "Client: please shorten the intro", "timecode_start": 1.5, "visibility": "public"})
reply = owner.request("POST", f"/assets/{asset}/comments/{public['id']}/replies", {
    "version_id": ff_version, "body": "Done in the next cut", "visibility": "public"})
owner.request("POST", f"/comments/{public['id']}/resolve", {})
internal = owner.request("POST", f"/assets/{asset}/comments", {
    "version_id": ff_version, "body": "Team only: music licence check", "timecode_start": 4.0, "visibility": "internal"})
print(json.dumps({"asset": str(asset), "version": ff_version, "comments": [public["id"], reply["id"], internal["id"]]}))
