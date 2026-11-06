"""One explicitly authorized SMTP invitation; never loop or retry this script."""
from datetime import timedelta
from django.utils import timezone
from apps.accounts.models import User
from apps.members.models import Invitation, OrgMembership
from apps.members.services import _send_invite_email
owner = User.objects.get(email='p1b24-owner@example.invalid')
org = OrgMembership.objects.get(user=owner).organization
org.name = '[JRC TEST] P1-B24'
org.save(update_fields=['name'])
invite = Invitation.objects.create(organization=org, invited_by=owner,
    email='jarvis+jrctest24@honzareznicek.com', expires_at=timezone.now()+timedelta(days=7))
assert _send_invite_email(invite), 'SMTP invitation was not accepted'
print('SMTP: one invitation accepted by upstream for jarvis+jrctest24@honzareznicek.com')
