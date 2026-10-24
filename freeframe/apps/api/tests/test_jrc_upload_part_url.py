"""F5-B05: the signed address of one upload part is short-lived."""
from urllib.parse import parse_qs, urlsplit

import boto3
import pytest

from apps.api.config import settings
from apps.api.services import s3_service


def _signing_client():
    return boto3.client(
        "s3", endpoint_url="http://127.0.0.1:14053", region_name="garage",
        aws_access_key_id="test", aws_secret_access_key="test",
        config=boto3.session.Config(s3={"addressing_style": "path"}),
    )


def test_default_lifetime_is_fifteen_minutes(monkeypatch):
    monkeypatch.setattr(s3_service, "_get_presign_client", _signing_client)
    url = s3_service.presign_upload_part("raw/p/a/v/original.mp4", "upload", 2)
    assert parse_qs(urlsplit(url).query)["X-Amz-Expires"] == ["900"]
    assert settings.upload_part_url_seconds == 900


def test_lifetime_follows_setting_and_explicit_value(monkeypatch):
    monkeypatch.setattr(s3_service, "_get_presign_client", _signing_client)
    monkeypatch.setattr(settings, "upload_part_url_seconds", 300)
    assert parse_qs(urlsplit(s3_service.presign_upload_part("k", "u", 1)).query)["X-Amz-Expires"] == ["300"]
    assert parse_qs(urlsplit(s3_service.presign_upload_part("k", "u", 1, expires_in=60)).query)["X-Amz-Expires"] == ["60"]


@pytest.mark.parametrize("value", [0, 59, 3601])
def test_setting_rejects_unsafe_lifetimes(value):
    from pydantic import ValidationError

    from apps.api.config import Settings

    with pytest.raises(ValidationError):
        Settings(upload_part_url_seconds=value)
