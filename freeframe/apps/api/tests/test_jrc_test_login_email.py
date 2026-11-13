"""Sanitized service logins must work without relaxing production validation."""
import pytest
from pydantic import ValidationError

from apps.api.schemas.auth import LoginRequest


@pytest.mark.parametrize("project", ["jrc-t", "jrc-p2b01"])
@pytest.mark.parametrize("address", ["a" * 64 + "@example.invalid", "jrc-sync@SUB.EXAMPLE.INVALID"])
def test_reserved_test_mailboxes_can_log_in_to_explicit_test_projects(monkeypatch, project, address):
    monkeypatch.setenv("JRC_PROJECT", project)
    result = LoginRequest(email=address, password="test-password")
    assert result.email == address.split("@")[0] + "@" + address.split("@")[1].lower()


@pytest.mark.parametrize("project", ["jrc", "", "other"])
def test_main_and_unidentified_projects_keep_emailstr_validation(monkeypatch, project):
    monkeypatch.setenv("JRC_PROJECT", project)
    with pytest.raises(ValidationError):
        LoginRequest(email="jrc-sync@example.invalid", password="test-password")
    assert LoginRequest(email="service@EXAMPLE.COM", password="test-password").email == "service@example.com"


@pytest.mark.parametrize("address", [
    "broken", "a@.invalid", "a@invalid", "a@bad..invalid", "a@@example.invalid",
    "a@example.invalid\nBcc: b@example.com", "a" * 65 + "@example.invalid",
])
def test_test_project_still_rejects_malformed_mailboxes(monkeypatch, address):
    monkeypatch.setenv("JRC_PROJECT", "jrc-p2b01")
    with pytest.raises(ValidationError):
        LoginRequest(email=address, password="test-password")
