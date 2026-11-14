"""Místní ověřovací relace rolí; bez opakovaných přihlášení do ochrany proti zahlcení."""
import json
import os
import sys

from django.conf import settings
from django.contrib.sessions.backends.db import SessionStore

from apps.accounts.models import User

assert os.environ.get('JRC_PROJECT') == 'jrc-p2b10'
fixture = json.load(sys.stdin)
for role in ['owner', 'manager', 'editor', 'client', 'viewer']:
    user = User.objects.get(email=fixture[role]['email'])
    session = SessionStore()
    session['_auth_user_id'] = str(user.pk)
    session['_auth_user_backend'] = 'django.contrib.auth.backends.ModelBackend'
    session['_auth_user_hash'] = user.get_session_auth_hash()
    session.save()
    fixture[role]['cookie'] = {'name': settings.SESSION_COOKIE_NAME, 'value': session.session_key}
print(json.dumps(fixture))
