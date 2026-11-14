# JR Content: přidáno 2026-11-14 (blok P2-B17), AGPL-3.0.
"""Pages for two-factor authentication: the login step and Settings → Security."""

from urllib.parse import quote

from allauth.account.stages import LoginStageController
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.debug import sensitive_post_parameters, sensitive_variables
from django.views.decorators.http import require_http_methods

from apps.accounts import twofactor
from apps.accounts.qr import qr_svg
from apps.accounts.stages import TwoFactorStage


def _no_store(response):
    # Secrets and recovery codes must not stay in the browser cache or history.
    response["Cache-Control"] = "no-store, max-age=0"
    response["Pragma"] = "no-cache"
    return response


@sensitive_post_parameters("code", "password")
@never_cache
@require_http_methods(["GET", "POST"])
def two_factor_login(request):
    """Second step of the login: code from the authenticator app or a recovery code."""
    stage = LoginStageController.enter(request, TwoFactorStage.key)
    if stage is None or stage.login.user is None:
        return redirect("account_login")
    user = stage.login.user
    if not twofactor.is_enabled(user):
        return stage.exit()

    error = ""
    status = 200
    mode = "app"
    if request.method == "POST":
        if request.POST.get("action") == "cancel":
            return stage.abort()
        mode = "recovery" if request.POST.get("mode") == "recovery" else "app"
        result = twofactor.verify(user, request.POST.get("code", ""), request=request)
        if result.ok:
            if result is twofactor.Result.RECOVERY_CODE:
                left = twofactor.recovery_codes_left(user)
                messages.warning(
                    request,
                    f"You signed in with a recovery code. {left} recovery code{'s' if left != 1 else ''} left. "
                    "Create new ones in Settings → Security.",
                )
            return stage.exit()
        error = twofactor.ERROR_MESSAGES[result]
        if result is twofactor.Result.INVALID and mode == "recovery":
            error = "That recovery code is not valid or was already used."
        status = 429 if result is twofactor.Result.LOCKED else 200

    return _no_store(
        render(
            request,
            "account/two_factor_login.html",
            {"error": error, "mode": mode, "login_email": user.email},
            status=status,
        )
    )


@sensitive_variables()
def _codes_page(request, codes, *, heading):
    text = "JR Content recovery codes\n" + request.user.email + "\n\n" + "\n".join(codes) + "\n"
    return _no_store(
        render(
            request,
            "accounts/security_recovery_codes.html",
            {
                "settings_active": "security",
                "codes": codes,
                "heading": heading,
                "download_href": "data:text/plain;charset=utf-8," + quote(text),
            },
        )
    )


@sensitive_post_parameters("code", "password")
@never_cache
@login_required
@require_http_methods(["GET", "POST"])
@sensitive_variables()
def security_settings(request):
    user = request.user
    errors = {}

    if request.method == "POST":
        action = request.POST.get("action", "")
        if action in ("start", "new_key") and not twofactor.is_enabled(user):
            twofactor.pending_secret(user, regenerate=action == "new_key")
            return redirect(request.path + "?step=setup")
        if action == "cancel_setup":
            twofactor.cancel_setup(user)
            return redirect(request.path)
        if action == "confirm" and not twofactor.is_enabled(user):
            result, codes = twofactor.confirm_setup(
                user,
                request.POST.get("code", ""),
                request=request,
                password=request.POST.get("password", ""),
                check_password=True,
            )
            if result.ok:
                return _codes_page(request, codes, heading="Two-factor authentication is on")
            errors["confirm"] = twofactor.ERROR_MESSAGES[result]
        elif action == "regenerate" and twofactor.is_enabled(user):
            result = twofactor.verify(user, request.POST.get("code", ""), request=request, allow_recovery=False)
            if result.ok:
                codes = twofactor.regenerate_recovery_codes(user, request=request)
                return _codes_page(request, codes, heading="New recovery codes")
            errors["regenerate"] = twofactor.ERROR_MESSAGES[result]
        elif action == "disable" and twofactor.is_enabled(user):
            result = twofactor.disable_with_code(
                user, request.POST.get("password", ""), request.POST.get("code", ""), request=request
            )
            if result.ok:
                messages.success(request, "Two-factor authentication is off.")
                return redirect(request.path)
            errors["disable"] = twofactor.ERROR_MESSAGES[result]
        elif not errors:
            return redirect(request.path)

    context = {
        "settings_active": "security",
        "enabled": twofactor.is_enabled(user),
        "errors": errors,
        "needs_password": user.has_usable_password(),
    }
    if context["enabled"]:
        context["recovery_codes_left"] = twofactor.recovery_codes_left(user)
        context["enabled_at"] = user.totp_enabled_at
    elif (request.GET.get("step") == "setup" or "confirm" in errors) and user.totp_secret:
        secret = user.totp_secret
        uri = twofactor.otpauth_uri(user, secret)
        context.update(
            setup=True,
            secret_display=twofactor.format_secret(secret),
            otpauth_uri=uri,
            qr_svg=qr_svg(uri, title="QR code for your authenticator app"),
        )
    return _no_store(render(request, "accounts/security.html", context, status=429 if _locked_error(errors) else 200))


def _locked_error(errors):
    return twofactor.ERROR_MESSAGES[twofactor.Result.LOCKED] in errors.values()
