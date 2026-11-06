"""Prepare local reset/confirmation links without delivering mail."""
import json
from pathlib import Path
from allauth.account import app_settings
from allauth.account.adapter import get_adapter
from allauth.account.internal.flows.password_reset import request_password_reset
from allauth.account.models import EmailAddress, EmailConfirmationHMAC
from django.core import mail
from django.test import RequestFactory, override_settings
from apps.accounts.models import User
fixture = json.loads(Path('/tmp/P1-B24-fixture.json').read_text())
user = User.objects.get(email=fixture['email'])
request = RequestFactory().get('/', HTTP_HOST='jrc-bb-web')
with override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend'):
    request_password_reset(request, user.email, [user], None)
    import re
    reset_url = re.search(r'http://127\.0\.0\.1:13663/jrc/accounts/password/reset/key/[^\s]+', mail.outbox[-1].body).group()
    address, _ = EmailAddress.objects.get_or_create(user=user, email=user.email, defaults={'primary':True})
    address.verified = False
    address.save()
    confirmation = EmailConfirmationHMAC(address)
    confirm_url = get_adapter().get_email_confirmation_url(request, confirmation)
    rendered = get_adapter().render_mail("account/email/email_confirmation", user.email, {"user":user, "activate_url":confirm_url})
    confirm_url = re.search(r"http://127\.0\.0\.1:13663/jrc/accounts/confirm-email/[^\s]+", rendered.body).group()
output = Path('/tmp/P1-B24-links.json')
output.touch(mode=0o600, exist_ok=True)
output.write_text(json.dumps({'reset_url':reset_url, 'confirm_url':confirm_url}))
print('Prepared local reset and confirmation links (not sent)')
