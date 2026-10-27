import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import pytest
from django.contrib.auth.models import AnonymousUser
from django.http import HttpResponse
from django.template import Context, Template
from django.test import RequestFactory, override_settings
from django.urls import get_script_prefix, set_script_prefix

from apps.members import services, views


@pytest.fixture(params=["", "/jrc"])
def prefix(request):
    old = get_script_prefix()
    set_script_prefix(request.param + "/")
    yield request.param
    set_script_prefix(old)


def test_invitation_email_and_anonymous_redirect_keep_prefix(prefix):
    invitation = SimpleNamespace(
        pk=1,
        email="invite@example.invalid",
        token="test-token",
        organization=SimpleNamespace(name="Org"),
        invited_by=None,
        workspace_assignments=[],
        is_expired=False,
        is_accepted=False,
    )
    with (
        patch.object(services, "_reserve_invite_send", return_value=object()),
        patch.object(services, "render_to_string", return_value="mail") as render,
        patch.object(services, "send_or_raise"),
        override_settings(APP_URL="http://testserver"),
    ):
        services._send_invite_email(invitation)
        assert (
            render.call_args_list[0].args[1]["accept_url"]
            == f"http://testserver{prefix}/members/invite/test-token/accept/"
        )
    request = RequestFactory().post("/members/invite/test-token/accept/")
    request.user = AnonymousUser()
    with (
        patch.object(views.Invitation.objects, "select_related") as query,
        override_settings(LOGIN_URL="account_login"),
    ):
        query.return_value.get.return_value = invitation
        result = views.accept_invite(request, "test-token")
    url = urlsplit(result.url)
    assert url.path == prefix + "/accounts/login/"
    assert parse_qs(url.query)["next"] == [prefix + "/members/invite/test-token/accept/"]


@pytest.mark.parametrize("invalid", [False, True])
def test_accept_invite_form_and_error_keep_script_prefix(prefix, invalid):
    invitation = SimpleNamespace(
        pk=1,
        email="invite@example.invalid",
        token="test-token",
        organization=SimpleNamespace(name="Org"),
        invited_by=None,
        workspace_assignments=[],
        is_expired=False,
        is_accepted=False,
    )
    request = RequestFactory().post("/members/invite/test-token/accept/") if invalid else RequestFactory().get("/")
    request.user = AnonymousUser()
    request.session = {}
    with patch.object(views.Invitation.objects, "select_related") as query:
        query.return_value.get.return_value = invitation
        with patch.object(views, "render", return_value=HttpResponse()) as render:
            if invalid:
                request.user = SimpleNamespace(is_authenticated=True)
                with patch.object(services, "accept_invitation", side_effect=ValueError("invalid invitation")):
                    views.accept_invite(request, "test-token")
            else:
                views.accept_invite(request, "test-token")

    expected = prefix + "/members/invite/test-token/accept/"
    assert render.call_args.args[2]["accept_url"] == expected


def test_dynamic_composer_and_delete_urls_use_named_routes(prefix):
    root = Path(__file__).resolve().parents[3] / "templates"
    for relative in ["composer/compose.html", "calendar/partials/_delete_post_modal.html"]:
        text = (root / relative).read_text()
        assert "/workspace/{{" not in text
        tags = re.findall(r"{% url \'composer:[^%]+%}", text)
        assert tags
        # Render every URL used by the dynamic replacements with valid UUIDs.
        zero = "00000000-0000-0000-0000-000000000000"
        dynamic = [tag for tag in tags if zero in tag]
        assert dynamic
        for tag in dynamic:
            url = Template(tag).render(Context({"workspace": SimpleNamespace(id=zero)}))
            assert url.startswith(prefix + "/workspace/" + zero + "/")
