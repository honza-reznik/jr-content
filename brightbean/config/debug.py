# JR Content: přidáno 2026-11-14 (blok P2-B17), AGPL-3.0.
from django.views.debug import SafeExceptionReporterFilter


class AlwaysCleansingFilter(SafeExceptionReporterFilter):
    """Hide variables and POST fields marked @sensitive_variables / @sensitive_post_parameters
    also when DEBUG is on (Django's default filter is switched off then, so the technical 500 page
    of a development run would show the 2FA secret and typed codes)."""

    def is_active(self, request):
        return True
