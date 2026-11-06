import json
import os
import secrets
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch
from django.core import mail
from django.test import RequestFactory, override_settings
from django.utils import timezone
from allauth.account.adapter import get_adapter
from apps.accounts.models import User
from apps.members.models import Invitation, OrgMembership
from apps.members.services import _send_invite_email
from apps.workspaces.models import Workspace

run_id = secrets.token_hex(6)
password = secrets.token_urlsafe(24)
owner = User.objects.create_user(email=f'p1b24-owner-{run_id}@example.invalid', password=secrets.token_urlsafe(24))
org = OrgMembership.objects.get(user=owner).organization
org.name = 'P1-B24 test'
org.save()
workspace = Workspace.objects.get(organization=org)
invite = Invitation.objects.create(organization=org, invited_by=owner, email=f'p1b24-client-{run_id}@example.invalid', expires_at=timezone.now()+timedelta(days=7), workspace_assignments=[{'workspace_id': str(workspace.pk), 'role': 'client'}])
request = RequestFactory().get('/', HTTP_HOST='127.0.0.1:13663')
with override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend'):
    _send_invite_email(invite)
    get_adapter().send_mail('account/email/unknown_account', 'unknown@example.invalid', {'request':request, 'email':'unknown@example.invalid', 'signup_url':'http://127.0.0.1:13663/jrc/accounts/signup/'})
    print(json.dumps([{'subject':m.subject, 'brightbean': 'brightbean' in m.body.lower() or 'brightbean' in m.subject.lower(), 'signup_link':'/accounts/signup/' in m.body} for m in mail.outbox]))
output = Path('/tmp/P1-B24-fixture.json')
output.touch(mode=0o600, exist_ok=True)
output.write_text(json.dumps({'token':invite.token, 'email':invite.email, 'password':password}))
