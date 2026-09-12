"""Tests for secure, refreshable token persistence."""

import json
import stat

from hyundai_kia_connect_api.Token import Token


def test_saved_token_keeps_refresh_data_but_omits_login_and_control_secrets(tmp_path):
    path = tmp_path / "session.json"
    token = Token(
        username="person@example.com",
        password="account-password",
        access_token="Bearer ccs-token",
        refresh_token="cci-refresh-token",
        pin="1234",
        control_token="short-lived-control-token",
        control_token_expiry=12345,
        cci_access_token="cci-access-token",
        exchangeable_refresh_token="exchangeable-refresh-token",
        non_ccs_refresh_token="non-ccs-refresh-token",
    )

    token.save(path)

    data = json.loads(path.read_text(encoding="utf-8"))
    restored = Token.load(path)
    assert data["password"] is None
    assert data["pin"] is None
    assert data["control_token"] is None
    assert data["control_token_expiry"] == 0
    assert restored.refresh_token == "cci-refresh-token"
    assert restored.exchangeable_refresh_token == "exchangeable-refresh-token"
    assert restored.non_ccs_refresh_token == "non-ccs-refresh-token"


def test_saved_token_file_is_readable_only_by_its_owner(tmp_path):
    path = tmp_path / "session.json"

    Token(access_token="Bearer token").save(path)

    assert stat.S_IMODE(path.stat().st_mode) == 0o600
