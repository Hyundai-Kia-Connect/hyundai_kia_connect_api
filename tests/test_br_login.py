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

from unittest.mock import MagicMock

import pytest

from hyundai_kia_connect_api.const import BRAND_HYUNDAI, BRANDS, REGION_BRAZIL, REGIONS
from hyundai_kia_connect_api.exceptions import (
    AuthenticationError,
    DeviceIDError,
    InvalidAPIResponseError,
)
from hyundai_kia_connect_api.HyundaiBlueLinkApiBR import HyundaiBlueLinkApiBR
from hyundai_kia_connect_api.Token import Token

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


class TestGetVehicles:
    """The vehicles call-site: BR now follows the Type1 pattern (``.json()`` +
    ``_check_response_for_errors``) instead of a bare ``raise_for_status()``,
    so a SPA-envelope 4xx surfaces a typed error instead of a raw ``HTTPError``.

    Regression for kia_uvo #1846 / #1395: ``GET /spa/vehicles`` returned a bare
    ``400 Bad Request`` (body discarded by ``raise_for_status``).
    """

    def test_400_spa_envelope_raises_typed_error_not_httperror(self, br_api):
        # SPA envelope (retCode F + resCode 4002) → DeviceIDError via the shared
        # classifier, preserving device-id retry semantics — not a raw HTTPError.
        # get_vehicles re-registers and retries once; when the retry fails the
        # same way, the typed error still surfaces.
        br_api.session = MagicMock()
        br_api.session.get.return_value = _resp(
            400, {"retCode": "F", "resCode": "4002", "resMsg": "Invalid deviceId"}
        )
        br_api._get_device_id = MagicMock(return_value="srv-1")
        with pytest.raises(DeviceIDError):
            br_api.get_vehicles(MagicMock())

    def test_400_unknown_shape_raises_apierror_not_httperror(self, br_api):
        # A non-SPA JSON body falls through the shared classifier to
        # InvalidAPIResponseError — still not a raw HTTPError.
        br_api.session = MagicMock()
        br_api.session.get.return_value = _resp(400, {"errCode": 4009, "errMsg": "x"})
        with pytest.raises(InvalidAPIResponseError):
            br_api.get_vehicles(MagicMock())

    def test_200_vehicles_returned(self, br_api):
        br_api.session = MagicMock()
        br_api.session.get.return_value = _resp(
            200,
            {
                "resMsg": {
                    "vehicles": [
                        {
                            "vehicleId": "v1",
                            "nickname": "My Car",
                            "vehicleName": "Creta",
                            "regDate": "20240101",
                            "vin": "VIN123",
                            "type": "GN",
                            "ccuCCS2ProtocolSupport": 0,
                        }
                    ]
                }
            },
        )
        vehicles = br_api.get_vehicles(MagicMock())
        assert len(vehicles) == 1
        assert vehicles[0].id == "v1"


class TestDeviceIdRegistration:
    """BR must use a server-issued device id, not a hardcoded/random one.

    Regression for kia_uvo #1861: the device id hardcoded in #962 was
    eventually invalidated server-side, so every authenticated SPA call
    answered ``resCode 4002`` ("Invalid deviceId") and the integration was
    stuck in ``setup_retry`` with all entities unavailable.
    """

    def test_no_hardcoded_device_id(self, br_api):
        assert not hasattr(br_api, "ccsp_device_id")
        assert br_api._registered_device_id is None

    def test_get_device_id_returns_server_issued_id(self, br_api):
        br_api.session = MagicMock()
        br_api.session.post.return_value = _resp(
            200, {"retCode": "S", "resCode": "0000", "resMsg": {"deviceId": "srv-1"}}
        )
        assert br_api._get_device_id() == "srv-1"

        url = br_api.session.post.call_args.args[0]
        assert url.endswith("/spa/notifications/register")
        payload = br_api.session.post.call_args.kwargs["json"]
        assert set(payload) == {"pushRegId", "pushType", "uuid"}
        assert len(payload["pushRegId"]) == 64

    def test_get_device_id_caches_for_reuse(self, br_api):
        br_api.session = MagicMock()
        br_api.session.post.return_value = _resp(
            200, {"retCode": "S", "resCode": "0000", "resMsg": {"deviceId": "srv-1"}}
        )
        assert br_api._ensure_device_id() == "srv-1"
        # Second call must not register another push device.
        assert br_api._ensure_device_id() == "srv-1"
        assert br_api.session.post.call_count == 1

    def test_get_device_id_surfaces_registration_failure(self, br_api):
        br_api.session = MagicMock()
        br_api.session.post.return_value = _resp(403, None, text="<html>blocked</html>")
        with pytest.raises(AuthenticationError, match="device registration"):
            br_api._get_device_id()

    def test_login_uses_registered_device_id(self, br_api):
        br_api.session = MagicMock()
        br_api._get_cookies = MagicMock(return_value={})
        br_api._get_authorization_code = MagicMock(return_value="code")
        br_api._get_auth_response = MagicMock(
            return_value={
                "access_token": "at",
                "refresh_token": "rt",
                "expires_in": 3600,
            }
        )
        br_api._get_device_id = MagicMock(return_value="srv-1")

        token = br_api.login("user@example.com", "pass")
        assert token.device_id == "srv-1"

    def test_get_vehicles_reregisters_and_retries_on_4002(self, br_api):
        """A device id invalidated mid-session must self-heal, not strand the
        integration until the next restart."""
        br_api.session = MagicMock()
        br_api.session.get.side_effect = [
            _resp(
                400, {"retCode": "F", "resCode": "4002", "resMsg": "Invalid deviceId"}
            ),
            _resp(
                200,
                {
                    "resMsg": {
                        "vehicles": [
                            {
                                "vehicleId": "v1",
                                "nickname": "My Car",
                                "vehicleName": "Creta",
                                "regDate": "20240101",
                                "vin": "VIN123",
                                "type": "GN",
                                "ccuCCS2ProtocolSupport": 0,
                            }
                        ]
                    }
                },
            ),
        ]
        br_api._get_device_id = MagicMock(return_value="srv-2")

        token = Token(access_token="at", device_id="stale-id")
        vehicles = br_api.get_vehicles(token)

        assert [v.id for v in vehicles] == ["v1"]
        assert token.device_id == "srv-2"
        br_api._get_device_id.assert_called_once()


class TestRefreshAccessToken:
    """BR must not inherit the Type1 refresh, which cannot work here.

    ``ApiImplType1.refresh_access_token`` reads ``USER_API_URL`` /
    ``BASE_URL`` / ``BASIC_AUTHORIZATION``; BR defines none of them, so every
    refresh raised ``AttributeError``, was swallowed by the helper's
    ``except Exception`` and degraded into a full login on every poll.
    """

    def test_refresh_uses_br_endpoint_and_preserves_device_id(self, br_api):
        br_api.session = MagicMock()
        br_api.session.post.return_value = _resp(
            200, {"access_token": "at2", "refresh_token": "rt2", "expires_in": 3600}
        )
        token = Token(
            username="user@example.com",
            password="pass",
            access_token="at1",
            refresh_token="rt1",
            device_id="srv-1",
            pin="1234",
        )

        new_token = br_api.refresh_access_token(token)

        url = br_api.session.post.call_args.args[0]
        assert url == "https://br-ccapi.hyundai.com.br/api/v1/user/oauth2/token"
        assert br_api.session.post.call_args.kwargs["data"]["grant_type"] == (
            "refresh_token"
        )
        # BR adds its own "Bearer " prefix in _get_authenticated_headers, so the
        # stored token must stay unprefixed (Type1 prepends token_type).
        assert new_token.access_token == "at2"
        assert new_token.refresh_token == "rt2"
        assert new_token.device_id == "srv-1"
        assert new_token.pin == "1234"

    def test_refresh_without_refresh_token_falls_back_to_login(self, br_api):
        br_api.login = MagicMock(return_value="logged-in")
        token = Token(username="user@example.com", password="pass", refresh_token=None)

        assert br_api.refresh_access_token(token) == "logged-in"
        # pin must be passed as a keyword; Type1 passed it positionally into
        # login()'s otp_handler slot.
        br_api.login.assert_called_once_with("user@example.com", "pass", pin=None)

    def test_refresh_failure_falls_back_to_login(self, br_api):
        br_api.session = MagicMock()
        br_api.session.post.return_value = _resp(
            400, {"errCode": 4001, "errMsg": "Invalid grant"}
        )
        br_api.login = MagicMock(return_value="logged-in")
        token = Token(username="user@example.com", password="pass", refresh_token="rt1")

        assert br_api.refresh_access_token(token) == "logged-in"
