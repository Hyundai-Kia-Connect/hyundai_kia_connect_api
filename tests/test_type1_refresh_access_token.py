"""Tests for ApiImplType1.refresh_access_token (shared by AU, IN, CN).

Locks in the unified base behaviour: the refresh_token grant is sent to
``oauth2/token`` without a Stamp header. CN's existing ``_get_refresh_token``
already omits Stamp and works, which shows the endpoint family does not
require it. AU and IN previously sent Stamp, but that was carried over from
``_get_access_token`` (authorization_code grant) and is not required for the
refresh_token grant. If a region's server turns out to require Stamp, the
call fails and falls back to ``self.login()`` — no regression vs. pre-PR.
"""

from unittest.mock import patch


from hyundai_kia_connect_api.ApiImplType1 import ApiImplType1
from hyundai_kia_connect_api.KiaUvoApiAU import KiaUvoApiAU
from hyundai_kia_connect_api.KiaUvoApiIN import KiaUvoApiIN
from hyundai_kia_connect_api.KiaUvoApiCN import KiaUvoApiCN
from hyundai_kia_connect_api.Token import Token

import datetime as dt


def _make_token(**overrides) -> Token:
    defaults = dict(
        username="user@test.com",
        password="MyPassword123!",
        access_token="Bearer old-access-token",
        refresh_token="OLDREFRESHTOKEN1234567890123456789012345678",
        device_id="device-123",
        valid_until=dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=1),
        pin="1234",
    )
    defaults.update(overrides)
    return Token(**defaults)


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


def _ok_payload():
    return {
        "retCode": "S",
        "resCode": "0000",
        "token_type": "Bearer",
        "access_token": "new-access-token",
        "refresh_token": "new-refresh-token",
        "expires_in": 86400,
    }


class TestType1RefreshAccessTokenNoStamp:
    """The base implementation must not send a Stamp header."""

    def setup_method(self):
        self.api = ApiImplType1()
        # Minimal attributes the base method reads.
        self.api.USER_API_URL = "https://example.test/api/v1/user/"
        self.api.BASE_URL = "example.test"
        self.api.BASIC_AUTHORIZATION = "Basic test=="

    def test_does_not_send_stamp_header(self):
        captured = {}

        def fake_post(url, data=None, headers=None, **kwargs):
            captured["url"] = url
            captured["data"] = data
            captured["headers"] = headers
            return _FakeResponse(_ok_payload())

        with patch(
            "hyundai_kia_connect_api.ApiImpl.ApiImplSession.post", side_effect=fake_post
        ):
            self.api.refresh_access_token(_make_token())

        assert "Stamp" not in captured["headers"], (
            "refresh_access_token must not send a Stamp header — CN proves the "
            "refresh_token grant does not require it"
        )
        # The base implementation never calls _get_stamp (CN has no _get_stamp),
        # so there is nothing to mock on the base class.

    def test_posts_to_oauth2_token_with_refresh_grant(self):
        captured = {}

        def fake_post(url, data=None, headers=None, **kwargs):
            captured["url"] = url
            captured["data"] = data
            return _FakeResponse(_ok_payload())

        with patch(
            "hyundai_kia_connect_api.ApiImpl.ApiImplSession.post", side_effect=fake_post
        ):
            self.api.refresh_access_token(_make_token())

        assert captured["url"] == "https://example.test/api/v1/user/oauth2/token"
        assert "grant_type=refresh_token" in captured["data"]
        assert "OLDREFRESHTOKEN1234567890123456789012345678" in captured["data"]

    def test_returns_new_token_preserving_device_id(self):
        with patch(
            "hyundai_kia_connect_api.ApiImpl.ApiImplSession.post",
            return_value=_FakeResponse(_ok_payload()),
        ):
            result = self.api.refresh_access_token(_make_token(device_id="keep-this"))

        assert result.access_token == "Bearer new-access-token"
        assert result.refresh_token == "new-refresh-token"
        assert result.device_id == "keep-this"
        assert result.username == "user@test.com"
        assert result.pin == "1234"

    def test_falls_back_to_login_on_exchange_failure(self):
        with (
            patch(
                "hyundai_kia_connect_api.ApiImpl.ApiImplSession.post",
                return_value=_FakeResponse(
                    {"retCode": "F", "resCode": "7501", "resMsg": "auth"}
                ),
            ),
            patch.object(
                ApiImplType1, "login", return_value="login-fallback"
            ) as mock_login,
        ):
            result = self.api.refresh_access_token(_make_token())

        mock_login.assert_called_once()
        assert result == "login-fallback"

    def test_falls_back_to_login_when_refresh_token_missing(self):
        with patch.object(
            ApiImplType1, "login", return_value="login-fallback"
        ) as mock_login:
            result = self.api.refresh_access_token(_make_token(refresh_token=None))
        mock_login.assert_called_once()
        assert result == "login-fallback"

    def test_preserves_old_refresh_token_when_response_omits_it(self):
        payload = _ok_payload()
        del payload["refresh_token"]
        with patch(
            "hyundai_kia_connect_api.ApiImpl.ApiImplSession.post",
            return_value=_FakeResponse(payload),
        ):
            result = self.api.refresh_access_token(
                _make_token(refresh_token="ORIGINAL-RT")
            )
        assert result.refresh_token == "ORIGINAL-RT"


class TestRegionsUseBaseRefreshAccessToken:
    """AU, IN, CN must inherit the base implementation (no per-region override)."""

    def test_au_does_not_override_refresh_access_token(self):
        # AU must use ApiImplType1.refresh_access_token, not its own.
        assert KiaUvoApiAU.refresh_access_token is ApiImplType1.refresh_access_token

    def test_in_does_not_override_refresh_access_token(self):
        assert KiaUvoApiIN.refresh_access_token is ApiImplType1.refresh_access_token

    def test_cn_does_not_override_refresh_access_token(self):
        assert KiaUvoApiCN.refresh_access_token is ApiImplType1.refresh_access_token

    def test_au_refresh_does_not_send_stamp(self):
        api = KiaUvoApiAU(region=5, brand=2, language="en")
        captured = {}

        def fake_post(url, data=None, headers=None, **kwargs):
            captured["headers"] = headers
            return _FakeResponse(_ok_payload())

        with patch(
            "hyundai_kia_connect_api.ApiImpl.ApiImplSession.post", side_effect=fake_post
        ):
            api.refresh_access_token(_make_token())

        assert "Stamp" not in captured["headers"]
