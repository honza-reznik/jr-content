"""Tests for HLS streaming proxy."""
import pytest
from unittest.mock import MagicMock, patch
import jwt
import time


class TestCreateHlsToken:
    """Tests for HLS token generation."""

    def test_creates_valid_jwt(self):
        from apps.api.routers.hls_proxy import create_hls_token
        from apps.api.config import settings

        token = create_hls_token("hls/project-1/version-1")
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])

        assert payload["sub"] == "hls"
        assert payload["pfx"] == "hls/project-1/version-1"
        assert "exp" in payload
        assert payload["v"] == 4
        assert 0 < payload["exp"] - time.time() <= 1800


class TestVerifyHlsToken:
    """Tests for HLS token verification."""

    def test_valid_token(self):
        from apps.api.routers.hls_proxy import create_hls_token, _verify_hls_token

        token = create_hls_token("hls/proj/ver")
        prefix = _verify_hls_token(token)
        assert prefix == "hls/proj/ver"

    def test_invalid_token_raises(self):
        from apps.api.routers.hls_proxy import _verify_hls_token

        with pytest.raises(Exception) as exc_info:
            _verify_hls_token("garbage-token")
        assert exc_info.value.status_code == 401

    def test_wrong_sub_raises(self):
        from apps.api.routers.hls_proxy import _verify_hls_token
        from apps.api.config import settings

        token = jwt.encode(
            {"sub": "not-hls", "pfx": "some/path"},
            settings.jwt_secret,
            algorithm=settings.jwt_algorithm,
        )
        with pytest.raises(Exception) as exc_info:
            _verify_hls_token(token)
        assert exc_info.value.status_code == 403


class TestRewriteManifest:
    """Tests for m3u8 manifest URL rewriting."""

    @patch("apps.api.routers.hls_proxy.generate_presigned_get_url")
    def test_rewrites_ts_to_presigned(self, mock_presign):
        mock_presign.return_value = "https://s3.example.com/presigned-segment.ts"

        from apps.api.routers.hls_proxy import _rewrite_manifest

        content = "#EXTM3U\n#EXT-X-VERSION:3\n#EXTINF:2.000,\nsegment0.ts\n#EXT-X-ENDLIST"
        result = _rewrite_manifest(content, "hls/proj/ver", "720p/index.m3u8")

        assert "https://s3.example.com/presigned-segment.ts" in result
        mock_presign.assert_called_once_with("hls/proj/ver/720p/segment0.ts", expires_in=1800)

    @patch("apps.api.routers.hls_proxy.generate_presigned_get_url")
    def test_rewrites_m3u8_to_proxy_url(self, mock_presign):
        from apps.api.routers.hls_proxy import _rewrite_manifest

        content = "#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=800000\n720p/index.m3u8"
        result = _rewrite_manifest(content, "hls/proj/ver", "master.m3u8")

        assert "720p/index.m3u8" in result
        mock_presign.assert_not_called()

    @patch("apps.api.routers.hls_proxy.generate_presigned_get_url")
    def test_preserves_comments_and_tags(self, mock_presign):
        from apps.api.routers.hls_proxy import _rewrite_manifest

        content = "#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-ENDLIST"
        result = _rewrite_manifest(content, "hls/proj/ver", "index.m3u8")

        assert result == content


class TestHlsProxyEndpoint:
    """Tests for directory traversal prevention."""

    def test_rejects_non_m3u8(self):
        from apps.api.routers.hls_proxy import hls_proxy, create_hls_token

        token = create_hls_token("hls/proj/ver")
        with pytest.raises(Exception) as exc_info:
            hls_proxy("segment0.ts", token=token)
        assert exc_info.value.status_code == 400

    def test_rejects_directory_traversal(self):
        from apps.api.routers.hls_proxy import hls_proxy, create_hls_token

        token = create_hls_token("hls/proj/ver")
        with pytest.raises(Exception) as exc_info:
            hls_proxy("../../etc/passwd.m3u8", token=token)
        assert exc_info.value.status_code == 400

    def test_rejects_absolute_path(self):
        from apps.api.routers.hls_proxy import hls_proxy, create_hls_token

        token = create_hls_token("hls/proj/ver")
        with pytest.raises(Exception) as exc_info:
            hls_proxy("/etc/passwd.m3u8", token=token)
        assert exc_info.value.status_code == 400


class TestSegmentUrlLifetime:
    """A segment URL never outlives the token that the playlist was fetched with.

    The proxy checks only the token's signature and expiry, never the asset, so
    the token's lifetime is the bound on how long a player can keep fetching
    video after access to it ended. Segment URLs cannot outlive the short HLS
    credential that authorized the playlist.
    """

    def _token(self, seconds_left):
        from apps.api.config import settings
        from datetime import datetime, timedelta, timezone
        return jwt.encode(
            {"sub": "hls", "v": 4, "pfx": "hls/proj/ver",
             "exp": datetime.now(timezone.utc) + timedelta(seconds=seconds_left)},
            settings.jwt_secret, algorithm=settings.jwt_algorithm,
        )

    def _signed_expiries(self, token):
        """Fetch a variant playlist through the proxy with real URL signing, and
        return each segment URL's X-Amz-Expires."""
        from urllib.parse import parse_qs, urlparse
        from apps.api.routers.hls_proxy import hls_proxy

        body = b"#EXTM3U\n#EXTINF:2.000,\nseg0.ts\n#EXTINF:2.000,\nseg1.ts\n#EXT-X-ENDLIST"
        s3 = MagicMock()
        s3.get_object.return_value = {"Body": MagicMock(read=MagicMock(return_value=body))}
        with patch("apps.api.routers.hls_proxy.get_s3_client", return_value=s3):
            response = hls_proxy("720p/index.m3u8", token=token)
        urls = [l for l in response.body.decode().split("\n") if l.startswith("http")]
        assert len(urls) == 2
        return [int(parse_qs(urlparse(u).query)["X-Amz-Expires"][0]) for u in urls]

    def test_a_token_near_its_end_signs_segments_only_for_what_is_left(self):
        for expires in self._signed_expiries(self._token(60)):
            assert 1 <= expires <= 60

    def test_a_fresh_token_signs_segments_for_its_full_lifetime(self):
        from apps.api.routers.hls_proxy import create_hls_token, HLS_TOKEN_SECONDS
        for expires in self._signed_expiries(create_hls_token("hls/proj/ver")):
            assert HLS_TOKEN_SECONDS - 60 <= expires <= HLS_TOKEN_SECONDS

    def test_an_expired_token_signs_nothing(self):
        from apps.api.routers.hls_proxy import hls_proxy
        with pytest.raises(Exception) as exc_info:
            hls_proxy("720p/index.m3u8", token=self._token(-5))
        assert exc_info.value.status_code == 401

    def test_previous_long_lived_hls_tokens_are_invalidated(self):
        from apps.api.config import settings
        from apps.api.routers.hls_proxy import hls_proxy
        legacy = jwt.encode(
            {"sub": "hls", "v": 3, "pfx": "hls/proj/ver",
             "exp": time.time() + 86400},
            settings.jwt_secret, algorithm=settings.jwt_algorithm,
        )
        with pytest.raises(Exception) as exc_info:
            hls_proxy("master.m3u8", token=legacy)
        assert exc_info.value.status_code == 401


class TestCookieHls:
    @pytest.mark.parametrize("root_path", ["", "/video-api"])
    @pytest.mark.parametrize("scheme", ["http", "https"])
    def test_credential_is_httponly_and_scoped_to_one_version(self, root_path, scheme):
        from fastapi import Request, Response
        from apps.api.routers.hls_proxy import issue_hls_url, HLS_TOKEN_SECONDS

        request = Request({"type": "http", "scheme": scheme, "root_path": root_path,
                           "path": "/assets/a/stream", "headers": [], "server": ("testserver", 80)})
        response = Response()
        url = issue_hls_url(request, response, "processed/video-v1")
        cookie = response.headers["set-cookie"]
        assert "?" not in url and "eyJ" not in url
        assert f'Path={root_path}{url.removesuffix("master.m3u8")}' in cookie
        assert "HttpOnly" in cookie and "SameSite=strict" in cookie
        assert f"Max-Age={HLS_TOKEN_SECONDS}" in cookie
        assert ("Secure" in cookie) == (scheme == "https")
        assert response.headers["cache-control"] == "no-store"

    def test_public_url_and_query_token_do_not_authorize_a_session(self, client):
        from apps.api.routers.hls_proxy import create_hls_token, hls_session_id

        token = create_hls_token("processed/v1")
        url = f'/stream/hls/{hls_session_id("processed/v1")}/master.m3u8'
        assert client.get(url).status_code == 401
        assert client.get(url + f"?token={token}").status_code == 401

    def test_cookie_does_not_authorize_a_different_version(self, client):
        from apps.api.routers.hls_proxy import create_hls_token, hls_session_id

        client.cookies.set("ff_hls", create_hls_token("processed/v1"))
        url = f'/stream/hls/{hls_session_id("processed/v2")}/master.m3u8'
        with patch("apps.api.routers.hls_proxy.get_s3_client") as storage:
            assert client.get(url).status_code == 403
            storage.assert_not_called()

    def test_two_versions_can_play_without_replacing_each_others_cookie(self, client):
        from apps.api.routers.hls_proxy import create_hls_token
        from .hls_helpers import hls_url

        first = hls_url(client, create_hls_token("processed/v1"), "master.m3u8")
        second = hls_url(client, create_hls_token("processed/v2"), "master.m3u8")
        body = MagicMock()
        body.read.return_value = b"#EXTM3U\n720p/index.m3u8"
        storage = MagicMock()
        storage.get_object.return_value = {"Body": body}
        with patch("apps.api.routers.hls_proxy.get_s3_client", return_value=storage):
            for url in [first, second, first]:
                result = client.get(url)
                assert result.status_code == 200
                assert result.text == "#EXTM3U\n720p/index.m3u8"
        assert [c.kwargs["Key"] for c in storage.get_object.call_args_list] == [
            "processed/v1/master.m3u8", "processed/v2/master.m3u8", "processed/v1/master.m3u8",
        ]

    def test_managed_segment_missing_from_storage_returns_404(self, client, test_user):
        import uuid
        from apps.api.routers.hls_proxy import create_hls_token
        from .hls_helpers import hls_url

        token = create_hls_token("processed/v1", managed=True, user_id=str(test_user.id),
                                 project_id=str(uuid.uuid4()), token_version=1)
        with (patch("apps.api.routers.hls_proxy.get_user_by_id", return_value=test_user),
              patch("apps.api.routers.hls_proxy.require_project_role"),
              patch("apps.api.routers.hls_proxy.get_s3_client") as storage):
            storage.return_value.get_object.side_effect = RuntimeError("private storage detail")
            response = client.get(hls_url(client, token, "720p/seg.ts"))
        assert response.status_code == 404
        assert response.json()["detail"] == "Segment not found"

    def test_managed_segments_use_bounded_chunks_and_close_body(self, client, test_user):
        import uuid
        from apps.api.routers.hls_proxy import create_hls_token, SEGMENT_CHUNK_BYTES
        from .hls_helpers import hls_url, segment_storage

        token = create_hls_token("processed/v1", managed=True, user_id=str(test_user.id),
                                 project_id=str(uuid.uuid4()), token_version=1)
        with (patch("apps.api.routers.hls_proxy.get_user_by_id", return_value=test_user),
              patch("apps.api.routers.hls_proxy.require_project_role"),
              segment_storage() as (storage, body),
              patch("apps.api.routers.hls_proxy.generate_presigned_get_url") as sign):
            response = client.get(hls_url(client, token, "720p/seg.ts"), follow_redirects=False)
        assert response.status_code == 200
        assert response.content == b"transport-stream"
        assert response.headers["content-type"] == "video/mp2t"
        assert response.headers["cache-control"] == "no-store"
        assert "location" not in response.headers
        sign.assert_not_called()
        body.read.assert_not_called()
        body.iter_chunks.assert_called_once_with(chunk_size=SEGMENT_CHUNK_BYTES)
        body.close.assert_called()
