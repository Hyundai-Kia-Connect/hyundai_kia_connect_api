"""Tests for Brazilian Hyundai BlueLink auth error handling.

Regression for kia_uvo #1792 / #1515: the BR signin endpoint returns HTTP 400
(with a JSON body describing the failure) for password-expired / blocked /
verification-required accounts. Previously ``raise_for_status()`` raised a raw
``HTTPError`` before the body was parsed, so users saw "400 Client Error: Bad
Request for url: .../user/signin" with no actionable reason.

These tests cover ``_raise_auth_error`` across the three BR auth call-sites
(``_get_cookies``, ``_get_authorization_code``, ``_get_auth_response``) plus the
existing HTTP 200 + ``{step:N}`` path (#1239) as a regression guard.
"""

from unittest.mock import MagicMock, patch

import pytest

from hyundai_kia_connect_api.const import BRAND_HYUNDAI, BRANDS, REGION_BRAZIL, REGIONS
from hyundai_kia_connect_api.exceptions import APIError, AuthenticationError
from hyundai_kia_connect_api.HyundaiBlueLinkApiBR import HyundaiBlueLinkApiBR

_BR_REGION = next(k for k, v in REGIONS.items() if v == REGION_BRAZIL)
_HYUNDAI_BRAND = next(k for k, v in BRANDS.items() if v == BRAND_HYUNDAI)


def _resp(status_code: int, json_data: dict | None = None, text: str = "") -> MagicMock:
    r = MagicMock()
    r.status_code = status_code
    if json_data is not None:
        r.json.return_value = json_data
    else:
        r.json.side_effect = ValueError("not JSON")
    r.text = text
    return r


@pytest.fixture
def br_api() -> HyundaiBlueLinkApiBR:
    return HyundaiBlueLinkApiBR(region=_BR_REGION, brand=_HYUNDAI_BRAND)


class TestRaiseAuthError:
    """Unit-test the helper directly."""

    def test_no_op_on_success(self, br_api):
        # 2xx must not raise.
        br_api._raise_auth_error(_resp(200, {"redirectUrl": "x"}), "signin")

    def test_400_step_surfaces_readable_reason(self, br_api):
        with pytest.raises(AuthenticationError, match="password has expired"):
            br_api._raise_auth_error(_resp(400, {"step": 5}), "signin")

    def test_400_errcode_errmsg(self, br_api):
        with pytest.raises(AuthenticationError, match="errCode=4003"):
            br_api._raise_auth_error(
                _resp(400, {"errCode": 4003, "errMsg": "Invalid credentials"}), "signin"
            )

    def test_400_non_json_falls_back_to_snippet(self, br_api):
        # Cloudflare / WAF HTML response, no JSON body.
        with pytest.raises(AuthenticationError, match="Response not JSON"):
            br_api._raise_auth_error(
                _resp(403, None, text="<html>Attention Required</html>"),
                "cookie request",
            )

    def test_400_unknown_shape_lists_keys_only(self, br_api):
        with pytest.raises(AuthenticationError, match="keys="):
            br_api._raise_auth_error(_resp(400, {"foo": "bar"}), "token request")


class TestGetAuthorizationCode:
    """The signin call-site: 4xx must surface the body, not a raw HTTPError."""

    def test_400_step5_raises_authentication_error_not_httperror(self, br_api):
        br_api.session = MagicMock()
        br_api.session.post.return_value = _resp(400, {"step": 5})
        with pytest.raises(AuthenticationError, match="password has expired"):
            br_api._get_authorization_code({}, "user@example.com", "pass")

    def test_400_errcode_raises_authentication_error(self, br_api):
        br_api.session = MagicMock()
        br_api.session.post.return_value = _resp(
            400, {"errCode": 4003, "errMsg": "Invalid credentials"}
        )
        with pytest.raises(AuthenticationError, match="errCode=4003"):
            br_api._get_authorization_code({}, "user@example.com", "pass")

    def test_400_non_json_raises_with_snippet(self, br_api):
        br_api.session = MagicMock()
        br_api.session.post.return_value = _resp(400, None, text="<html>blocked</html>")
        with pytest.raises(AuthenticationError, match="Response not JSON"):
            br_api._get_authorization_code({}, "user@example.com", "pass")

    def test_200_step5_still_handled_regression_guard(self, br_api):
        """#1239 fixed the HTTP 200 + {step:N} path; this guard ensures the new
        4xx helper did not break it."""
        br_api.session = MagicMock()
        br_api.session.post.return_value = _resp(200, {"step": 5})
        with pytest.raises(AuthenticationError, match="password has expired"):
            br_api._get_authorization_code({}, "user@example.com", "pass")

    def test_200_redirect_url_returns_code(self, br_api):
        br_api.session = MagicMock()
        br_api.session.post.return_value = _resp(
            200, {"redirectUrl": "https://br-ccapi.hyundai.com.br/cb?code=ABC123"}
        )
        code = br_api._get_authorization_code({}, "user@example.com", "pass")
        assert code == "ABC123"


class TestGetCookies:
    def test_400_non_json_raises_authentication_error(self, br_api):
        br_api.session = MagicMock()
        br_api.session.get.return_value = _resp(400, None, text="<html>error</html>")
        with pytest.raises(AuthenticationError, match="cookie request"):
            br_api._get_cookies()


class TestGetAuthResponse:
    def test_400_errcode_raises_authentication_error(self, br_api):
        br_api.session = MagicMock()
        br_api.session.post.return_value = _resp(
            400, {"errCode": 4001, "errMsg": "Invalid grant"}
        )
        with pytest.raises(AuthenticationError, match="token request"):
            br_api._get_auth_response("some-auth-code")


class TestDeviceRegistration:
    def test_registers_device_with_android_contract(self, br_api):
        br_api.session = MagicMock()
        br_api._registration_uuid = "local-uuid"
        br_api.session.post.return_value = _resp(
            200,
            {
                "retCode": "S",
                "resCode": "0000",
                "resMsg": {"deviceId": "registered-device"},
            },
        )

        with patch(
            "hyundai_kia_connect_api.HyundaiBlueLinkApiBR.time.time",
            return_value=123.456,
        ):
            device_id = br_api._get_device_id()

        assert device_id == "registered-device"
        assert br_api.ccsp_device_id == "registered-device"
        (url,) = br_api.session.post.call_args.args
        request = br_api.session.post.call_args.kwargs
        assert url.endswith("/api/v1/spa/notifications/register")
        assert request["headers"] == {
            "Accept": "application/json",
            "Content-Type": "application/json; charset=UTF-8",
            "User-Agent": "okhttp/4.12.0",
            "ccsp-service-id": "03f7df9b-7626-4853-b7bd-ad1e8d722bd5",
            "ccsp-application-id": "213a491a-0d7c-4d6a-ac03-a2df127d73b0",
            "offset": "-3",
        }
        assert request["json"] == {
            "uuid": "local-uuid",
            "pushRegId": "dummy-push-123456",
            "pushType": "GCM",
        }

    def test_reuses_registered_device_in_same_instance(self, br_api):
        br_api.session = MagicMock()
        br_api.session.post.return_value = _resp(
            200,
            {
                "retCode": "S",
                "resCode": "0000",
                "resMsg": {"deviceId": "registered-device"},
            },
        )

        assert br_api._get_device_id() == "registered-device"
        assert br_api._get_device_id() == "registered-device"
        assert br_api.session.post.call_count == 1

    @pytest.mark.parametrize(
        ("status_code", "data", "message"),
        [
            (500, {"retCode": "F", "resCode": "5000"}, "HTTP 500"),
            (200, {"retCode": "F", "resCode": "4002"}, "was rejected"),
            (200, {"retCode": "S", "resCode": "0000", "resMsg": {}}, "deviceId"),
            (200, None, "invalid JSON"),
            (200, [], "unexpected JSON"),
        ],
    )
    def test_rejects_invalid_registration_responses(
        self, br_api, status_code, data, message
    ):
        br_api.session = MagicMock()
        br_api.session.post.return_value = _resp(status_code, data)

        with pytest.raises(APIError, match=message):
            br_api._get_device_id()
        assert br_api.ccsp_device_id is None

    def test_login_uses_registered_device_id_in_token(self, br_api):
        br_api._get_device_id = MagicMock(return_value="registered-device")
        br_api._get_cookies = MagicMock(return_value={})
        br_api._get_authorization_code = MagicMock(return_value="authorization-code")
        br_api._get_auth_response = MagicMock(
            return_value={
                "access_token": "access-token",
                "refresh_token": "refresh-token",
                "expires_in": 3600,
            }
        )

        token = br_api.login("user@example.com", "password", pin="1234")

        assert token.device_id == "registered-device"
        br_api._get_device_id.assert_called_once_with()
