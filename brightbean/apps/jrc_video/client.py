"""Server-to-server FreeFrame API. Tokens live only in this client's memory."""

import hashlib
import threading
import time
import uuid

import requests
from django.conf import settings

_TOKENS = {}
_TOKEN_LOCK = threading.RLock()


class FreeFrameError(Exception):
    def __init__(self, message, *, status_code=None):
        super().__init__(message)
        self.status_code = status_code


class FreeFrameClient:
    def __init__(self):
        self.base_url = settings.JRC_FF_API_URL.rstrip("/")
        self.token = None
        self.key = (
            self.base_url,
            settings.JRC_FF_SYNC_EMAIL,
            hashlib.sha256(settings.JRC_FF_SYNC_PASSWORD.encode()).digest(),
        )

    def request(self, method, path, payload=None):
        self._authenticate()
        try:
            result = self._send(method, path, payload)
        except FreeFrameError as exc:
            if exc.status_code != 401:
                raise
            self._authenticate(renew=True)
            result = self._send(method, path, payload)
        self._validate(path, method, result)
        return result

    @staticmethod
    def _validate(path, method, result):
        try:
            if path.startswith("/jrc-sync/"):
                uuid.UUID(result["id"])
                if path.startswith("/jrc-sync/projects/"):
                    uuid.UUID(result["service_user_id"])
            elif method == "GET" and path.endswith("/members"):
                if not isinstance(result, list):
                    raise ValueError
                for member in result:
                    uuid.UUID(member["user_id"])
                    if member["role"] not in {"owner", "editor", "reviewer", "viewer"}:
                        raise ValueError
        except (KeyError, ValueError, TypeError, AttributeError):
            raise FreeFrameError("FreeFrame API returned an invalid synchronization response.") from None

    def _authenticate(self, *, renew=False):
        with _TOKEN_LOCK:
            cached = _TOKENS.get(self.key)
            if cached and (not renew or cached["access_token"] != self.token):
                self.token = cached["access_token"]
                return
            result = None
            if cached and cached.get("refresh_token"):
                try:
                    result = self._send("POST", "/auth/refresh", {"refresh_token": cached["refresh_token"]})
                except FreeFrameError as exc:
                    if exc.status_code != 401:
                        raise
            if result is None:
                result = self._send(
                    "POST",
                    "/auth/login",
                    {"email": settings.JRC_FF_SYNC_EMAIL, "password": settings.JRC_FF_SYNC_PASSWORD},
                )
            if (
                not isinstance(result, dict)
                or not isinstance(result.get("access_token"), str)
                or not result["access_token"]
            ):
                raise FreeFrameError("FreeFrame API returned an invalid login response.")
            _TOKENS[self.key] = result
            self.token = result["access_token"]

    def _send(self, method, path, payload):
        # Paths are relative to the fixed internal API; never another host (e.g. "@evil").
        if not isinstance(path, str) or not path.startswith("/"):
            raise FreeFrameError("Invalid FreeFrame API path.")
        try:
            response = requests.request(
                method,
                self.base_url + path,
                json=payload,
                headers={"Authorization": f"Bearer {self.token}"} if self.token else {},
                timeout=(3, 15),
                allow_redirects=False,
                # The internal API must never use a host HTTP proxy.
                proxies={"http": "", "https": ""},
            )
        except requests.RequestException:
            raise FreeFrameError("FreeFrame API is unavailable; run jrc_sync_freeframe to retry.") from None
        if not 200 <= response.status_code < 300:
            # Do not expose response bodies, invite tokens, passwords or headers.
            raise FreeFrameError(
                f"FreeFrame API returned HTTP {response.status_code} for {method} {path}.",
                status_code=response.status_code,
            )
        if response.status_code == 204:
            return None
        try:
            return response.json()
        except ValueError:
            raise FreeFrameError("FreeFrame API returned invalid JSON.") from None


# On-behalf access tokens (F5-B02), only in this process's memory: user pk -> token, expiry.
_USER_TOKENS = {}
_USER_TOKEN_LOCK = threading.RLock()
# Renew this long before FreeFrame's access token expires.
USER_TOKEN_MARGIN_SECONDS = 60


class FreeFrameUserClient(FreeFrameClient):
    """Calls the FreeFrame API on behalf of one Brightbean user (podklad F5-B05).

    The server exchanges an ``api`` ticket directly at jrc-ff-api:8000, so FreeFrame
    records the real author and enforces the user's own project role. FreeFrame
    issues only a short access token (no refresh token) for this target.
    """

    def __init__(self, user):
        self.base_url = settings.JRC_FF_API_URL.rstrip("/")
        self.user = user
        self.token = None
        self.key = (self.base_url, str(user.pk))

    def request(self, method, path, payload=None):
        from django.contrib.auth import get_user_model

        if not isinstance(path, str) or not path.startswith("/"):
            raise FreeFrameError("Invalid FreeFrame API path.")
        # Fail closed even before FreeFrame learns about a block (sync lag).
        if not get_user_model().objects.filter(pk=self.user.pk, is_active=True).exists():
            self.forget()
            raise FreeFrameError("The user is inactive; FreeFrame calls on their behalf are refused.")
        self._authenticate()
        try:
            return self._send(method, path, payload)
        except FreeFrameError as exc:
            if exc.status_code != 401:
                raise
            self._authenticate(renew=True)
            return self._send(method, path, payload)

    def forget(self):
        with _USER_TOKEN_LOCK:
            _USER_TOKENS.pop(self.key, None)
        self.token = None

    def _authenticate(self, *, renew=False):
        from .tickets import TicketError, issue_ticket

        with _USER_TOKEN_LOCK:
            cached = _USER_TOKENS.get(self.key)
            if (
                cached
                and cached["expires_at"] > time.monotonic()
                and not (renew and cached["access_token"] == self.token)
            ):
                self.token = cached["access_token"]
                return
            _USER_TOKENS.pop(self.key, None)
            self.token = None
            try:
                ticket = issue_ticket(self.user, audience="api")
            except TicketError as exc:
                raise FreeFrameError(str(exc)) from None
            result = self._send("POST", "/auth/jrc-exchange", {"ticket": ticket})
            if (
                not isinstance(result, dict)
                or not isinstance(result.get("access_token"), str)
                or not result["access_token"]
                or not isinstance(result.get("expires_in"), int)
                or isinstance(result.get("expires_in"), bool)
                or result["expires_in"] <= 0
                or "refresh_token" in result
            ):
                raise FreeFrameError("FreeFrame API returned an invalid ticket exchange response.")
            lifetime = max(result["expires_in"] - USER_TOKEN_MARGIN_SECONDS, 0)
            _USER_TOKENS[self.key] = {
                "access_token": result["access_token"],
                "expires_at": time.monotonic() + lifetime,
            }
            self.token = result["access_token"]
