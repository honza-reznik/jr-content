# JR Content: přidáno 2026-11-14 (blok P2-B17), AGPL-3.0.
"""Login stage that asks for the second factor before the session is created.

allauth runs its login stages after the password (or a social login, or the
login after a password reset when that is enabled) and *before* it calls
Django's ``login()``. While a stage is pending the visitor is anonymous, so
nothing behind ``login_required`` - FreeFrame sign-in tickets included - is
reachable until the code is accepted. Added to the stage list by
``AccountAdapter.get_login_stages``.
"""

from allauth.account.stages import LoginStage
from allauth.core.internal.httpkit import headed_redirect_response

from apps.accounts import twofactor


class TwoFactorStage(LoginStage):
    key = "jrc_two_factor"
    urlname = "accounts:two_factor_login"

    def handle(self):
        user = self.login.user
        if user is None or not twofactor.is_enabled(user):
            return None, True
        return headed_redirect_response(self.urlname), True
