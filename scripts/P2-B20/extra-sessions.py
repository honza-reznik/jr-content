"""Doplní nečlena do lokálních ověřovacích dat; jen jrc-p2b20."""
import json
import os

from django.test import Client
from django.utils import timezone

from apps.accounts.models import User
from apps.composer.models import Post

assert os.environ["JRC_PROJECT"] == "jrc-p2b20"
person, _ = User.objects.get_or_create(email='nonmember-p2b20@example.invalid', defaults={'name': 'Nonmember', 'tos_accepted_at': timezone.now()})
http = Client()
http.force_login(person)
Post.objects.get(workspace__organization__name="P2-B20 test studio", title="Drop target decoy").platform_posts.update(status="approved")
print(json.dumps({'session': http.session.session_key}))
