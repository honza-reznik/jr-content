"""P1-B16: pending invitations display their remaining validity."""

from datetime import timedelta
from unittest.mock import patch

import pytest
from django.template.loader import render_to_string
from django.utils import timezone, translation
from django.utils.formats import date_format

from apps.members.services import INVITE_EXPIRY_DAYS, create_invitation


@pytest.mark.django_db
@pytest.mark.parametrize("zone", ["UTC", "Europe/Prague"])
def test_new_invitation_has_seven_days_and_displays_remaining_time(organization, org_owner, zone):
    now = timezone.now()
    with patch("django.utils.timezone.now", return_value=now), timezone.override(zone), translation.override("en"):
        invitation = create_invitation(
            org=organization,
            email="invitee@example.invalid",
            org_role="member",
            workspace_assignments=[],
            invited_by=org_owner,
            inviter=org_owner,
        )
        invitation.refresh_from_db()
        assert invitation.expires_at == now + timedelta(days=INVITE_EXPIRY_DAYS)
        assert not invitation.is_expired
        html = render_to_string("members/partials/invitation_row.html", {"invite": invitation})
        # Django keeps each quantity together with a non-breaking space.
        html = html.replace("\xa0", " ")
        assert "Expires in 1 week" in html or "Expires in 6 days" in html
        assert "0 minutes" not in html
        assert date_format(timezone.localtime(invitation.expires_at), "M j, Y, H:i T") in html
    with patch("django.utils.timezone.now", return_value=now + timedelta(days=8)):
        assert invitation.is_expired
