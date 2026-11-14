"""Call the bounded relay, then validate actual pixels before private storage."""

import http.client

from django.core.exceptions import ValidationError

from .icons import MAX_BYTES, validated_favicon


def fetch_favicon(profile_url):
    if not profile_url:
        raise ValidationError("Add a profile link before refreshing the website icon.")
    connection = http.client.HTTPConnection("jrc-favicon-relay", 8090, timeout=10)
    try:
        connection.request("POST", "/favicon", body=profile_url.encode("utf-8"))
        response = connection.getresponse()
        if response.status != 200:
            raise ValidationError("Website icon unavailable. Check the profile link or upload your own image.")
        return validated_favicon(response.read(MAX_BYTES + 1))
    except (OSError, http.client.HTTPException):
        raise ValidationError("Website icon unavailable. Try again later or upload your own image.") from None
    finally:
        connection.close()


def refresh_favicon(account_id, source_url):
    from django.db import transaction

    from .models import ChannelProfile

    if not ChannelProfile.objects.filter(
        pk=account_id, social_account__platform="custom", profile_url=source_url
    ).exists():
        return False
    image = fetch_favicon(source_url)
    with transaction.atomic():
        profile = ChannelProfile.objects.select_for_update().filter(pk=account_id, profile_url=source_url).first()
        if profile is None:
            return False
        old_name, storage = profile.favicon.name, profile.favicon.storage
        profile.favicon = image
        profile.save(update_fields=["favicon", "updated_at"])
        if old_name:
            transaction.on_commit(lambda: storage.delete(old_name))
    return True
