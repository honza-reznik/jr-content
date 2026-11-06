"""Account mail uses deployment configuration, never a stale django.contrib.sites row."""

from types import SimpleNamespace
from urllib.parse import urlsplit, urlunsplit

from django.conf import settings
from django.urls import reverse


def account_mail_context(context):
    context = {**context, "signup_mode": settings.SIGNUP_MODE}
    origin = getattr(settings, "JRC_PUBLIC_ORIGIN", "").rstrip("/")
    if origin:
        base = getattr(settings, "JRC_BASE_PATH", "").rstrip("/")
        context["current_site"] = SimpleNamespace(name="JR Content", domain=urlsplit(origin).netloc)
        context["jrc_home_url"] = origin + base + "/"
        # Keep tokens and queries; use exactly one deployment prefix even when
        # allauth generated the link from an internal request host or old Site.
        for key in ("activate_url", "password_reset_url", "signup_url"):
            value = context.get(key)
            if value:
                parsed = urlsplit(value)
                path = parsed.path
                if base and not (path == base or path.startswith(base + "/")):
                    path = base + "/" + path.lstrip("/")
                public = urlsplit(origin)
                context[key] = urlunsplit((public.scheme, public.netloc, path, parsed.query, parsed.fragment))
    else:
        context["current_site"] = SimpleNamespace(name="JR Content", domain="")
        context["jrc_home_url"] = getattr(settings, "APP_URL", "").rstrip("/") + reverse("dashboard")
    # Reset requests do not prove an invitation; a mailed signup link must work
    # even if opened in a different browser without the invitation session.
    if settings.SIGNUP_MODE != "open":
        context["signup_url"] = ""
    return context
