"""Kontrola obnovy a přihlášení bez výpisu adres nebo hesel."""
import hashlib
import json
import urllib.request
from unittest.mock import patch

from allauth.account.models import EmailAddress
from django.conf import settings
from django.core.mail import EmailMessage

from apps.accounts.models import User
from apps.common.models import OutgoingEmail
from apps.members.models import Invitation
from apps.notifications.models import NotificationDelivery
from apps.jrc_video.tickets import issue_ticket

fixture = json.load(open('/fixture.json'))
assert settings.JRC_PROJECT == 'jrc-p2b01'
assert settings.EMAIL_BACKEND == 'apps.common.mail.BudgetedEmailBackend'
assert settings.EMAIL_INNER_BACKEND == 'django.core.mail.backends.console.EmailBackend'
with patch('apps.common.mail.BudgetedEmailBackend._inner') as transport:
    assert EmailMessage('Test', 'Test', to=['blocked-person@example.org']).send() == 0
transport.assert_not_called()
print('OK Compose: identita projektu, SMTP vypnuté a skutečný backend blokuje nepovoleného příjemce')
for model in (User, EmailAddress, Invitation):
    assert not model.objects.exclude(email__iendswith='.invalid').exists(), model.__name__
assert not OutgoingEmail.objects.exists()
assert not NotificationDelivery.objects.filter(channel='email', status__in=['pending', 'failed']).exists()
user = User.objects.get(pk=fixture['real_id'])
assert user.password == fixture['real_password_hash']
assert user.email == hashlib.sha256(b'restored-person@example.org').hexdigest() + '@example.invalid'
for who in ('owner', 'client'):
    record = fixture[who]
    user = User.objects.get(pk=record['id'])
    assert user.email == record['email']
    assert user.check_password(record['password'])
print('OK BB: adresy, fronty, hesla, původní ID a testovací účty')
for user_id in (fixture['owner']['id'], fixture['client']['id'], fixture['real_id']):
    ticket = issue_ticket(User.objects.get(pk=user_id), audience='api')
    request = urllib.request.Request(
        settings.JRC_FF_API_URL + '/auth/jrc-exchange',
        data=json.dumps({'ticket': ticket}).encode(),
        headers={'Content-Type': 'application/json'},
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        assert response.status == 200
        assert json.load(response).get('access_token')
print('OK BB↔FF: přihlášení přes vstupenku funguje pro všechny 3 spravované účty')
