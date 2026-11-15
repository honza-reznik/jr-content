from django.urls import path

from . import views, views_twofactor
from .views_signup import InviteClaimSocialSignupView, InvitePrefillSignupView, SignupLinkLoginView

app_name = "accounts"

urlpatterns = [
    path("login/", SignupLinkLoginView.as_view(), name="account_login"),
    path("signup/", InvitePrefillSignupView.as_view(), name="account_signup"),
    path("3rdparty/signup/", InviteClaimSocialSignupView.as_view(), name="socialaccount_signup"),
    path("accept-terms/", views.accept_terms, name="accept_terms"),
    path("settings/", views.account_settings, name="settings"),
    # JR Content P2-B17: two-factor authentication.
    path("settings/security/", views_twofactor.security_settings, name="security"),
    path("two-factor/", views_twofactor.two_factor_login, name="two_factor_login"),
    path("logout/", views.logout_view, name="logout"),
    path("review-banner/dismiss/", views.dismiss_review_banner, name="dismiss_review_banner"),
]
