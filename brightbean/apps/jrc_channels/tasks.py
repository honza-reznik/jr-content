"""One bounded attempt after adding or changing a custom channel's link."""

from background_task import background
from django.core.exceptions import ValidationError

from .favicons import refresh_favicon


@background(schedule=0)
def load_website_icon(account_id, source_url):
    try:
        refresh_favicon(account_id, source_url)
    except ValidationError:
        # No automatic retries against an unavailable site; manual refresh is in settings.
        return


def schedule_website_icon(account):
    from django.db import transaction

    if account.platform == "custom" and account.jrc_channel.profile_url:
        account_id, source_url = str(account.pk), account.jrc_channel.profile_url
        transaction.on_commit(lambda: load_website_icon(account_id, source_url))
