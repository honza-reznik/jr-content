# JR Content: přidáno 2026-11-14 (blok P2-B17), AGPL-3.0.
"""Two-factor authentication: TOTP (RFC 6238), QR code, recovery codes, login step,
rate limit, password reset, FreeFrame SSO, owner reset and the other ways in."""

import datetime
import hashlib
import logging
import re

import pytest
from allauth.account.forms import default_token_generator
from allauth.account.utils import user_pk_to_url_str
from django.core.cache import cache
from django.db import connection
from django.urls import reverse
from django.utils import timezone

from apps.accounts import twofactor
from apps.accounts.models import TwoFactorEvent, User
from apps.accounts.qr import QrCode, _num_data_codewords, qr_svg
from apps.members.models import OrgMembership, WorkspaceMembership
from apps.organizations.models import Organization
from apps.workspaces.models import Workspace

PASSWORD = "testpass123"
NOW = 1_800_000_015.0  # fixed clock, 15 s into a 30 s step


class Clock:
    def __init__(self, now):
        self.now = now

    def time(self):
        return self.now


@pytest.fixture(autouse=True)
def _clean_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def clock(monkeypatch):
    fake = Clock(NOW)
    monkeypatch.setattr(twofactor, "time", fake)
    return fake


def code_at(secret, at, offset_steps=0):
    return twofactor.totp(twofactor._secret_bytes(secret), at + offset_steps * twofactor.PERIOD)


def enable_2fa(user, clock):
    """Turn 2FA on through the service; returns (secret, recovery codes). Uses the step before NOW."""
    secret = twofactor.pending_secret(user)
    result, codes = twofactor.confirm_setup(user, code_at(secret, clock.now, -1))
    assert result is twofactor.Result.TOTP
    user.refresh_from_db()
    return secret, codes


def login_password(client, email, password=PASSWORD, next_url=None):
    data = {"login": email, "password": password}
    url = reverse("account_login")
    if next_url:
        url += f"?next={next_url}"
    return client.post(url, data)


def is_logged_in(client):
    return "_auth_user_id" in client.session


# -- RFC 4226 / RFC 6238 ------------------------------------------------------------


class TestRfcVectors:
    def test_hotp_rfc4226_appendix_d(self):
        key = b"12345678901234567890"
        expected = ["755224", "287082", "359152", "969429", "338314", "254676", "287922", "162583", "399871", "520489"]
        assert [twofactor.hotp(key, counter) for counter in range(10)] == expected

    @pytest.mark.parametrize(
        ("digest", "key", "vectors"),
        [
            (
                hashlib.sha1,
                b"12345678901234567890",
                {
                    59: "94287082",
                    1111111109: "07081804",
                    1111111111: "14050471",
                    1234567890: "89005924",
                    2000000000: "69279037",
                    20000000000: "65353130",
                },
            ),
            (
                hashlib.sha256,
                b"12345678901234567890123456789012",
                {
                    59: "46119246",
                    1111111109: "68084774",
                    1111111111: "67062674",
                    1234567890: "91819424",
                    2000000000: "90698825",
                    20000000000: "77737706",
                },
            ),
            (
                hashlib.sha512,
                b"1234567890" * 6 + b"1234",
                {
                    59: "90693936",
                    1111111109: "25091201",
                    1111111111: "99943326",
                    1234567890: "93441116",
                    2000000000: "38618901",
                    20000000000: "47863826",
                },
            ),
        ],
    )
    def test_totp_rfc6238_appendix_b(self, digest, key, vectors):
        for at, expected in vectors.items():
            assert twofactor.totp(key, at, digits=8, digest=digest) == expected

    def test_tolerance_is_one_step_either_side(self):
        secret = twofactor.generate_secret()
        current = int(NOW) // 30
        assert twofactor.matching_counter(secret, code_at(secret, NOW), now=NOW) == current
        assert twofactor.matching_counter(secret, code_at(secret, NOW, -1), now=NOW) == current - 1
        assert twofactor.matching_counter(secret, code_at(secret, NOW, +1), now=NOW) == current + 1
        for far in (-3, -2, 2, 3):
            code = code_at(secret, NOW, far)
            if code not in {code_at(secret, NOW, k) for k in (-1, 0, 1)}:
                assert twofactor.matching_counter(secret, code, now=NOW) is None

    def test_code_format(self):
        secret = twofactor.generate_secret()
        code = code_at(secret, NOW)
        assert twofactor.matching_counter(secret, f" {code[:3]} {code[3:]} ", now=NOW) is not None
        assert twofactor.matching_counter(secret, "abcdef", now=NOW) is None
        assert twofactor.matching_counter(secret, code + "0", now=NOW) is None
        assert twofactor.matching_counter(secret, "", now=NOW) is None

    def test_secret_and_uri(self):
        secret = twofactor.generate_secret()
        assert re.fullmatch(r"[A-Z2-7]{32}", secret)
        assert len(twofactor._secret_bytes(secret)) == 20
        assert secret != twofactor.generate_secret()
        user = User(email="ella+test@example.invalid")
        uri = twofactor.otpauth_uri(user, secret)
        assert uri == (
            "otpauth://totp/JR%20Content%3Aella%2Btest%40example.invalid"
            f"?secret={secret}&issuer=JR%20Content"
        )
        assert twofactor.format_secret("ABCDEFGHIJKL") == "ABCD EFGH IJKL"


# -- QR code -----------------------------------------------------------------------


def _matrix_hash(code):
    rows = ["".join("1" if cell else "0" for cell in row) for row in code.modules]
    return hashlib.sha256("\n".join(rows).encode()).hexdigest()


class TestQrCode:
    # Expected matrices produced by an independent encoder (QRCode.js by Kazuhiko Arase, bundled with npm),
    # for the same text, level, version and mask.
    @pytest.mark.parametrize(
        ("text", "ecl", "mask", "version", "digest"),
        [
            (
                "otpauth://totp/JR%20Content%3Aella%40example.invalid?secret=JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP&issuer=JR%20Content",
                "M",
                3,
                7,
                "eb20b15018199f1f31f0788f9be26a956c40ea80d40c2e1729c60fd1bf87db20",
            ),
            ("HELLO WORLD", "Q", 2, 1, "6e8544fb296df439a42489ed1fbdaf596109802eebe06ee5d89db070ef2ad423"),
            ("jrc-" * 50, "Q", 5, 12, "346028753a5d06ec1785c59df8a069941e0d417b6c94cf8dd69d8bcfbce4d3ff"),
            ("x" * 1000, "L", 6, 22, "4193c04f8810ec21d0532c8392a1339d927781397e863f3669507ddc48ae248d"),
        ],
    )
    def test_matches_independent_encoder(self, text, ecl, mask, version, digest):
        code = QrCode(text.encode(), ecl=ecl, mask=mask)
        assert code.version == version
        assert code.size == version * 4 + 17
        assert _matrix_hash(code) == digest

    def test_capacity_table_matches_standard(self):
        # Data codewords from ISO/IEC 18004 table 9 (spot checks across the range).
        expected = {(1, "L"): 19, (1, "M"): 16, (1, "Q"): 13, (1, "H"): 9, (10, "M"): 216, (15, "H"): 223}
        expected |= {(40, "L"): 2956, (40, "M"): 2334, (40, "Q"): 1666, (40, "H"): 1276}
        for (version, ecl), count in expected.items():
            assert _num_data_codewords(version, ecl) == count

    def test_svg_and_limits(self):
        svg = qr_svg("otpauth://totp/x?secret=ABC", title='QR "code" <x>')
        assert svg.startswith("<svg") and svg.endswith("</svg>")
        assert "&lt;x&gt;" in svg and "<x>" not in svg
        assert re.search(r"<path d=\"(M\d+,\d+h\d+v1h-\d+z)+\"", svg)
        with pytest.raises(ValueError):
            QrCode(b"x" * 3000, ecl="L")


# -- setup in Settings → Security ----------------------------------------------------------


@pytest.mark.django_db
class TestSetup:
    def test_page_needs_login(self, client):
        response = client.get(reverse("accounts:security"))
        assert response.status_code == 302
        assert reverse("account_login") in response["Location"]

    def test_full_setup(self, client, user, clock):
        client.force_login(user)
        page = client.get(reverse("accounts:security"))
        assert page.status_code == 200
        assert b"Set up two-factor authentication" in page.content
        assert b"Security" in page.content

        response = client.post(reverse("accounts:security"), {"action": "start"})
        assert response.status_code == 302 and response["Location"].endswith("?step=setup")
        user.refresh_from_db()
        secret = user.totp_secret
        assert secret and not user.totp_enabled

        setup = client.get(reverse("accounts:security") + "?step=setup")
        html = setup.content.decode()
        assert "<svg" in html and twofactor.format_secret(secret) in html
        assert "otpauth://totp/" in html
        assert "no-store" in setup["Cache-Control"]
        # The secret is encrypted at rest.
        with connection.cursor() as cursor:
            cursor.execute("SELECT totp_secret FROM accounts_user WHERE id = %s", [user.pk])
            raw = cursor.fetchone()[0]
        assert secret not in raw

        bad = client.post(reverse("accounts:security"), {"action": "confirm", "code": "000000", "password": PASSWORD})
        assert b"not valid" in bad.content
        user.refresh_from_db()
        assert not user.totp_enabled

        wrong_password = client.post(
            reverse("accounts:security"),
            {"action": "confirm", "code": code_at(secret, NOW), "password": "nope"},
        )
        assert b"password is incorrect" in wrong_password.content
        user.refresh_from_db()
        assert not user.totp_enabled

        done = client.post(
            reverse("accounts:security"), {"action": "confirm", "code": code_at(secret, NOW), "password": PASSWORD}
        )
        assert done.status_code == 200
        assert "no-store" in done["Cache-Control"]
        codes = re.findall(r"<li>([a-z0-9]{5}-[a-z0-9]{5})</li>", done.content.decode())
        assert len(codes) == 10 and len(set(codes)) == 10
        user.refresh_from_db()
        assert user.totp_enabled and user.totp_enabled_at
        # Only keyed hashes are stored, never the codes.
        assert len(user.totp_recovery_codes) == 10
        stored = " ".join(user.totp_recovery_codes)
        assert all(code not in stored and code.replace("-", "") not in stored for code in codes)
        assert TwoFactorEvent.objects.filter(user=user, action="enabled").count() == 1
        # The codes are shown once: the page afterwards shows only how many are left.
        after = client.get(reverse("accounts:security")).content.decode()
        assert "10 of 10 left" in after
        assert not any(code in after for code in codes)

    def test_new_key_and_cancel(self, client, user, clock):
        client.force_login(user)
        client.post(reverse("accounts:security"), {"action": "start"})
        user.refresh_from_db()
        first = user.totp_secret
        client.post(reverse("accounts:security"), {"action": "start"})
        user.refresh_from_db()
        assert user.totp_secret == first  # reload keeps the key already scanned
        client.post(reverse("accounts:security"), {"action": "new_key"})
        user.refresh_from_db()
        assert user.totp_secret != first
        client.post(reverse("accounts:security"), {"action": "cancel_setup"})
        user.refresh_from_db()
        assert user.totp_secret is None and not user.totp_enabled

    def test_secret_never_logged(self, client, user, clock, caplog):
        caplog.set_level(logging.DEBUG)
        client.force_login(user)
        client.post(reverse("accounts:security"), {"action": "start"})
        user.refresh_from_db()
        secret = user.totp_secret
        client.get(reverse("accounts:security") + "?step=setup")
        response = client.post(
            reverse("accounts:security"), {"action": "confirm", "code": code_at(secret, NOW), "password": PASSWORD}
        )
        codes = re.findall(r"<li>([a-z0-9]{5}-[a-z0-9]{5})</li>", response.content.decode())
        client.logout()
        login_password(client, user.email)
        client.post(reverse("accounts:two_factor_login"), {"code": codes[0], "mode": "recovery"})
        assert secret not in caplog.text
        assert not any(code in caplog.text for code in codes)


# -- login step -------------------------------------------------------------------------


@pytest.mark.django_db
class TestLogin:
    def test_without_2fa_login_is_unchanged(self, client, user):
        response = login_password(client, user.email)
        assert response.status_code == 302
        assert response["Location"] != reverse("accounts:two_factor_login")
        assert is_logged_in(client)

    def test_password_alone_does_not_log_in(self, client, user, clock):
        secret, _ = enable_2fa(user, clock)
        response = login_password(client, user.email, next_url="/settings/")
        assert response.status_code == 302
        assert response["Location"] == reverse("accounts:two_factor_login")
        assert not is_logged_in(client)
        assert client.get(reverse("accounts:settings")).status_code == 302

        page = client.get(reverse("accounts:two_factor_login"))
        assert page.status_code == 200
        assert b"Two-factor authentication" in page.content
        assert "no-store" in page["Cache-Control"]

        wrong = client.post(reverse("accounts:two_factor_login"), {"code": "123456"})
        assert wrong.status_code == 200 and b"not valid" in wrong.content
        assert not is_logged_in(client)

        ok = client.post(reverse("accounts:two_factor_login"), {"code": code_at(secret, NOW)})
        assert ok.status_code == 302
        assert ok["Location"] == "/settings/"
        assert is_logged_in(client)

    def test_wrong_password_never_reaches_code_step(self, client, user, clock):
        enable_2fa(user, clock)
        response = login_password(client, user.email, password="wrong")
        assert response.status_code == 200
        assert client.get(reverse("accounts:two_factor_login"))["Location"] == reverse("account_login")

    def test_code_step_without_password_redirects(self, client):
        response = client.get(reverse("accounts:two_factor_login"))
        assert response.status_code == 302 and response["Location"] == reverse("account_login")
        response = client.post(reverse("accounts:two_factor_login"), {"code": "123456"})
        assert response["Location"] == reverse("account_login")

    def test_cancel_clears_pending_login(self, client, user, clock):
        secret, _ = enable_2fa(user, clock)
        login_password(client, user.email)
        response = client.post(reverse("accounts:two_factor_login"), {"action": "cancel"})
        assert response["Location"] == reverse("account_login")
        response = client.post(reverse("accounts:two_factor_login"), {"code": code_at(secret, NOW)})
        assert response["Location"] == reverse("account_login")
        assert not is_logged_in(client)

    def test_code_cannot_be_replayed(self, client, user, clock):
        secret, _ = enable_2fa(user, clock)
        code = code_at(secret, NOW)
        login_password(client, user.email)
        assert client.post(reverse("accounts:two_factor_login"), {"code": code}).status_code == 302
        client.logout()
        login_password(client, user.email)
        replay = client.post(reverse("accounts:two_factor_login"), {"code": code})
        assert replay.status_code == 200 and b"already used" in replay.content
        assert not is_logged_in(client)
        # An older code of the tolerance window is refused as well.
        assert twofactor.verify(user, code_at(secret, NOW, -1)) is twofactor.Result.REUSED
        # The next time step works.
        clock.now += 30
        assert client.post(reverse("accounts:two_factor_login"), {"code": code_at(secret, clock.now)}).status_code == 302

    def test_tolerance_at_login(self, client, user, clock):
        secret, _ = enable_2fa(user, clock)  # consumed step NOW-1
        login_password(client, user.email)
        late_clock = code_at(secret, NOW, +1)  # phone 30 s ahead
        assert client.post(reverse("accounts:two_factor_login"), {"code": late_clock}).status_code == 302

    def test_recovery_code_works_once(self, client, user, clock):
        _, codes = enable_2fa(user, clock)
        login_password(client, user.email)
        response = client.post(reverse("accounts:two_factor_login"), {"code": codes[0].upper(), "mode": "recovery"})
        assert response.status_code == 302 and is_logged_in(client)
        user.refresh_from_db()
        assert twofactor.recovery_codes_left(user) == 9
        assert TwoFactorEvent.objects.filter(user=user, action="recovery_code_used").count() == 1
        client.logout()
        login_password(client, user.email)
        again = client.post(reverse("accounts:two_factor_login"), {"code": codes[0], "mode": "recovery"})
        assert again.status_code == 200 and b"not valid or was already used" in again.content
        assert client.post(reverse("accounts:two_factor_login"), {"code": codes[1].replace("-", "")}).status_code == 302

    def test_recovery_code_of_other_user_is_refused(self, client, user, clock):
        _, codes = enable_2fa(user, clock)
        other = User.objects.create_user(email="other@example.com", password=PASSWORD, tos_accepted_at=timezone.now())
        enable_2fa(other, clock)
        login_password(client, other.email)
        response = client.post(reverse("accounts:two_factor_login"), {"code": codes[0]})
        assert response.status_code == 200 and not is_logged_in(client)


# -- rate limit ------------------------------------------------------------------------


@pytest.mark.django_db
class TestRateLimit:
    def test_five_failures_lock_even_the_right_code(self, client, user, clock):
        secret, _ = enable_2fa(user, clock)
        login_password(client, user.email)
        for _ in range(5):
            assert client.post(reverse("accounts:two_factor_login"), {"code": "000000"}).status_code == 200
        locked = client.post(reverse("accounts:two_factor_login"), {"code": code_at(secret, NOW)})
        assert locked.status_code == 429
        assert b"Too many incorrect attempts" in locked.content
        assert not is_logged_in(client)
        assert TwoFactorEvent.objects.filter(user=user, action="locked").count() == 1
        # A fresh password login does not reset the budget.
        login_password(client, user.email)
        assert client.post(reverse("accounts:two_factor_login"), {"code": code_at(secret, NOW)}).status_code == 429

    def test_recovery_codes_count_against_the_budget(self, user, clock):
        _, codes = enable_2fa(user, clock)
        for _ in range(5):
            assert twofactor.verify(user, "zzzzz-zzzzz") is twofactor.Result.INVALID
        assert twofactor.verify(user, codes[0]) is twofactor.Result.LOCKED
        user.refresh_from_db()
        assert twofactor.recovery_codes_left(user) == 10

    def test_daily_limit(self, user, clock):
        secret, _ = enable_2fa(user, clock)
        short_key = f"jrc2fa:fail:{user.pk}:{15 * 60}"
        for _ in range(20):
            twofactor.verify(user, "000000")
            cache.delete(short_key)  # simulate the 15-minute windows passing
        assert twofactor.verify(user, code_at(secret, NOW)) is twofactor.Result.LOCKED

    def test_budget_is_per_user(self, client, user, clock):
        enable_2fa(user, clock)
        other = User.objects.create_user(email="other@example.com", password=PASSWORD, tos_accepted_at=timezone.now())
        other_secret, _ = enable_2fa(other, clock)
        for _ in range(6):
            twofactor.verify(user, "000000")
        assert twofactor.verify(other, code_at(other_secret, NOW)) is twofactor.Result.TOTP

    def test_success_clears_failures(self, user, clock):
        secret, _ = enable_2fa(user, clock)
        for _ in range(4):
            twofactor.verify(user, "000000")
        assert twofactor.verify(user, code_at(secret, NOW)) is twofactor.Result.TOTP
        for _ in range(4):
            twofactor.verify(user, "000000")
        clock.now += 30
        assert twofactor.verify(user, code_at(secret, clock.now)) is twofactor.Result.TOTP


# -- password reset --------------------------------------------------------------------


def _reset_password(client, user, new_password):
    key = default_token_generator.make_token(user)
    url = reverse("account_reset_password_from_key", kwargs={"uidb36": user_pk_to_url_str(user), "key": key})
    response = client.get(url)
    assert response.status_code == 302  # allauth moves the key into the session
    response = client.post(response["Location"], {"password1": new_password, "password2": new_password})
    assert response.status_code == 302
    return response


@pytest.mark.django_db
class TestPasswordReset:
    NEW = "brand-new-Passw0rd!"

    def test_reset_keeps_2fa(self, client, user, clock):
        secret, _ = enable_2fa(user, clock)
        _reset_password(client, user, self.NEW)
        assert not is_logged_in(client)
        user.refresh_from_db()
        assert user.totp_enabled and user.check_password(self.NEW)
        response = login_password(client, user.email, password=self.NEW)
        assert response["Location"] == reverse("accounts:two_factor_login")
        assert not is_logged_in(client)
        assert client.post(reverse("accounts:two_factor_login"), {"code": code_at(secret, NOW)}).status_code == 302
        assert is_logged_in(client)

    def test_login_on_reset_still_asks_for_code(self, client, user, clock, settings):
        settings.ACCOUNT_LOGIN_ON_PASSWORD_RESET = True
        enable_2fa(user, clock)
        response = _reset_password(client, user, self.NEW)
        assert response["Location"] == reverse("accounts:two_factor_login")
        assert not is_logged_in(client)

    def test_reset_lifts_the_lockout(self, client, user, clock):
        secret, _ = enable_2fa(user, clock)
        for _ in range(5):
            twofactor.verify(user, "000000")
        assert twofactor.is_locked(user)
        _reset_password(client, user, self.NEW)
        assert not twofactor.is_locked(user)


# -- FreeFrame SSO, admin login, magic link ----------------------------------------------------


@pytest.mark.django_db
class TestOtherEntrances:
    def test_video_sso_needs_completed_login(self, client, user, clock):
        secret, _ = enable_2fa(user, clock)
        workspace = Workspace.objects.create(organization=Organization.objects.create(name="Video Org"), name="Video")
        WorkspaceMembership.objects.create(user=user, workspace=workspace, workspace_role="editor")
        login_password(client, user.email)
        for method in (client.get, client.post):
            response = method(reverse("video_sso"), {"next": "/projects"})
            assert response.status_code == 302
            assert reverse("account_login") in response["Location"]
            assert "ticket" not in response["Location"]
        client.post(reverse("accounts:two_factor_login"), {"code": code_at(secret, NOW)})
        assert client.get(reverse("video_sso"), {"next": "/projects"}).status_code == 200

    def test_admin_login_goes_through_app_login(self, client, user, clock):
        user.is_staff = user.is_superuser = True
        user.save()
        enable_2fa(user, clock)
        response = client.get("/admin/login/")
        assert response.status_code == 302
        assert response["Location"].startswith(reverse("account_login"))
        response = client.post("/admin/login/", {"username": user.email, "password": PASSWORD})
        assert response.status_code == 302 and not is_logged_in(client)

    def test_magic_link_does_not_replace_the_code(self, client, user, clock):
        from apps.client_portal.models import MagicLinkToken

        enable_2fa(user, clock)
        workspace = Workspace.objects.create(organization=Organization.objects.create(name="Portal Org"), name="WS")
        token = MagicLinkToken.objects.create(
            user=user, workspace=workspace, expires_at=timezone.now() + datetime.timedelta(days=1)
        )
        response = client.post(reverse("client_portal:magic_link_entry", args=[token.token]))
        assert response.status_code == 302 and response["Location"] == reverse("account_login")
        assert not is_logged_in(client)


# -- disable, regenerate ----------------------------------------------------------------


@pytest.mark.django_db
class TestManage:
    def test_disable_needs_password_and_code(self, client, user, clock):
        secret, codes = enable_2fa(user, clock)
        client.force_login(user)
        url = reverse("accounts:security")
        response = client.post(url, {"action": "disable", "password": "wrong", "code": code_at(secret, NOW)})
        assert b"password is incorrect" in response.content
        response = client.post(url, {"action": "disable", "password": PASSWORD, "code": "000000"})
        assert b"not valid" in response.content
        user.refresh_from_db()
        assert user.totp_enabled
        response = client.post(url, {"action": "disable", "password": PASSWORD, "code": codes[0]})
        assert response.status_code == 302
        user.refresh_from_db()
        assert not user.totp_enabled
        assert user.totp_secret is None and user.totp_recovery_codes is None and user.totp_last_counter is None
        assert TwoFactorEvent.objects.filter(user=user, action="disabled").count() == 1
        client.logout()
        assert login_password(client, user.email)["Location"] != reverse("accounts:two_factor_login")

    def test_regenerate_needs_app_code_and_replaces_codes(self, client, user, clock):
        secret, old_codes = enable_2fa(user, clock)
        client.force_login(user)
        url = reverse("accounts:security")
        refused = client.post(url, {"action": "regenerate", "code": old_codes[0]})
        assert b"not valid" in refused.content
        response = client.post(url, {"action": "regenerate", "code": code_at(secret, NOW)})
        new_codes = re.findall(r"<li>([a-z0-9]{5}-[a-z0-9]{5})</li>", response.content.decode())
        assert len(new_codes) == 10 and not set(new_codes) & set(old_codes)
        user.refresh_from_db()
        assert twofactor.verify(user, old_codes[1]) is twofactor.Result.INVALID
        assert twofactor.verify(user, new_codes[0]) is twofactor.Result.RECOVERY_CODE
        assert TwoFactorEvent.objects.filter(user=user, action="recovery_codes_regenerated").count() == 1

    def test_wrong_passwords_count_against_the_budget(self, client, user, clock):
        secret, _ = enable_2fa(user, clock)
        client.force_login(user)
        for _ in range(5):
            client.post(reverse("accounts:security"), {"action": "disable", "password": "x", "code": "1"})
        response = client.post(
            reverse("accounts:security"), {"action": "disable", "password": PASSWORD, "code": code_at(secret, NOW)}
        )
        assert response.status_code == 429
        user.refresh_from_db()
        assert user.totp_enabled


# -- owner reset -----------------------------------------------------------------------


def _member(email, org, role):
    user = User.objects.create_user(email=email, password=PASSWORD, tos_accepted_at=timezone.now())
    auto = list(OrgMembership.objects.filter(user=user).values_list("organization_id", flat=True))
    WorkspaceMembership.objects.filter(user=user).delete()
    OrgMembership.objects.filter(user=user).delete()
    Organization.objects.filter(id__in=auto).delete()
    return OrgMembership.objects.create(user=user, organization=org, org_role=role)


@pytest.mark.django_db
class TestOwnerReset:
    @pytest.fixture
    def org(self):
        return Organization.objects.create(name="JRC Org")

    def _post(self, client, org, membership):
        return client.post(
            reverse("members:reset_two_factor", args=[membership.pk]),
            HTTP_HX_REQUEST="true",
            HTTP_X_JRC_ORGANIZATION_ID=str(org.pk),
        )

    def test_owner_resets_member(self, client, org, clock):
        owner = _member("owner@example.com", org, "owner")
        member = _member("member@example.com", org, "member")
        enable_2fa(member.user, clock)
        client.force_login(owner.user)
        listing = client.get(reverse("members:list"), HTTP_X_JRC_ORGANIZATION_ID=str(org.pk))
        assert b"Reset two-factor authentication" in listing.content
        assert b'data-testid="member-2fa"' in listing.content
        response = self._post(client, org, member)
        assert response.status_code == 200
        assert b'data-testid="member-2fa"' not in response.content
        member.user.refresh_from_db()
        assert not member.user.totp_enabled and member.user.totp_secret is None
        event = TwoFactorEvent.objects.get(user=member.user, action="reset_by_admin")
        assert event.actor == owner.user and event.actor_email == "owner@example.com" and event.organization == org

    def test_admin_cannot_reset(self, client, org, clock):
        admin = _member("admin@example.com", org, "admin")
        member = _member("member@example.com", org, "member")
        enable_2fa(member.user, clock)
        client.force_login(admin.user)
        listing = client.get(reverse("members:list"), HTTP_X_JRC_ORGANIZATION_ID=str(org.pk))
        assert b"Reset two-factor authentication" not in listing.content
        assert self._post(client, org, member).status_code == 403
        member.user.refresh_from_db()
        assert member.user.totp_enabled
        assert not TwoFactorEvent.objects.filter(action="reset_by_admin").exists()

    def test_owner_of_other_org_cannot_reset(self, client, org, clock):
        other_org = Organization.objects.create(name="Other")
        other_owner = _member("boss@example.com", other_org, "owner")
        member = _member("member@example.com", org, "member")
        enable_2fa(member.user, clock)
        client.force_login(other_owner.user)
        assert self._post(client, other_org, member).status_code == 404
        member.user.refresh_from_db()
        assert member.user.totp_enabled

    def test_owner_cannot_reset_self_here(self, client, org, clock):
        owner = _member("owner@example.com", org, "owner")
        enable_2fa(owner.user, clock)
        client.force_login(owner.user)
        assert self._post(client, org, owner).status_code == 422
        owner.user.refresh_from_db()
        assert owner.user.totp_enabled
