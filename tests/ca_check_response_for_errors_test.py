"""Tests for KiaUvoApiCA error handling: _check_response_for_errors + check_action_status.

Covers:
- known CA error codes map to typed exceptions
- 7110 (OTP required) passes through, handled in login
- malformed / unexpected response shapes raise typed APIError instead of
  crashing with KeyError (kia_uvo #1707 / #1819 crash class)
"""

import datetime as dt
from unittest.mock import MagicMock

import pytest

from hyundai_kia_connect_api.const import ORDER_STATUS
from hyundai_kia_connect_api.exceptions import (
    APIError,
    AuthenticationError,
    InvalidAPIResponseError,
)
from hyundai_kia_connect_api.KiaUvoApiCA import KiaUvoApiCA
from hyundai_kia_connect_api.Token import Token
from hyundai_kia_connect_api.Vehicle import Vehicle


class TestCheckResponseForErrors:
    @pytest.fixture
    def api(self) -> KiaUvoApiCA:
        return KiaUvoApiCA.__new__(KiaUvoApiCA)

    def test_success_response_passes(self, api):
        api._check_response_for_errors({"responseHeader": {"responseCode": 0}})

    def test_known_auth_error_raises_authentication_error(self, api):
        with pytest.raises(AuthenticationError, match="Wrong username/password"):
            api._check_response_for_errors(
                {
                    "responseHeader": {"responseCode": 1},
                    "error": {
                        "errorCode": "7404",
                        "errorDesc": "Wrong username/password",
                    },
                }
            )

    def test_7110_otp_required_passes_through(self, api):
        """7110 is OTP-required, handled in login — must not raise here."""
        api._check_response_for_errors(
            {
                "responseHeader": {"responseCode": 1},
                "error": {"errorCode": "7110", "errorDesc": "OTP required"},
            }
        )

    def test_unknown_error_code_raises_api_error(self, api):
        with pytest.raises(APIError, match="Request could not be processed"):
            api._check_response_for_errors(
                {
                    "responseHeader": {"responseCode": 1},
                    "error": {
                        "errorCode": "7445",
                        "errorDesc": "Request could not be processed",
                    },
                }
            )

    def test_unknown_error_code_without_desc_includes_code(self, api):
        """errorDesc absent -> the errorCode itself lands in the message."""
        with pytest.raises(APIError, match="7445"):
            api._check_response_for_errors(
                {
                    "responseHeader": {"responseCode": 1},
                    "error": {"errorCode": "7445"},
                }
            )

    def test_missing_error_key_raises_api_error(self, api):
        """responseCode==1 without an error block: typed error, not KeyError."""
        with pytest.raises(APIError, match="Unexpected error response shape"):
            api._check_response_for_errors({"responseHeader": {"responseCode": 1}})

    def test_non_dict_error_raises_api_error(self, api):
        with pytest.raises(APIError, match="Unexpected error response shape"):
            api._check_response_for_errors(
                {"responseHeader": {"responseCode": 1}, "error": 1}
            )

    def test_missing_response_header_raises_api_error(self, api):
        """No responseHeader at all: typed error instead of silent success."""
        with pytest.raises(APIError, match="Unexpected response shape"):
            api._check_response_for_errors({})


class TestCheckActionStatus:
    @pytest.fixture
    def api(self) -> KiaUvoApiCA:
        api = KiaUvoApiCA.__new__(KiaUvoApiCA)
        api.API_URL = "https://example.com/"
        api.API_HEADERS = {}
        api._sessions = MagicMock()
        api._get_pin_token = MagicMock(return_value="pin-token")
        return api

    @pytest.fixture
    def token(self) -> MagicMock:
        return MagicMock(spec=Token)

    @pytest.fixture
    def vehicle(self) -> Vehicle:
        return Vehicle(
            id="test-id",
            name="Tucson",
            model="Tucson",
            key="test-key",
            timezone=dt.timezone(dt.timedelta(hours=-5)),
        )

    def test_success(self, api, token, vehicle):
        api._sessions.post.return_value = MagicMock(
            json=lambda: {
                "responseHeader": {"responseCode": 0},
                "result": {"transaction": {"apiResult": "C", "apiStatusCode": "1"}},
            }
        )
        assert api.check_action_status(token, vehicle, "t1") is ORDER_STATUS.SUCCESS

    def test_error_envelope_returns_failed(self, api, token, vehicle):
        """responseCode==1 (error envelope, no result block) -> FAILED, no KeyError."""
        api._sessions.post.return_value = MagicMock(
            json=lambda: {
                "responseHeader": {"responseCode": 1},
                "error": {"errorCode": "7445", "errorDesc": "Request failed"},
            }
        )
        assert api.check_action_status(token, vehicle, "t1") is ORDER_STATUS.FAILED

    def test_missing_result_raises_invalid_api_response(self, api, token, vehicle):
        """Regression for #1819: response without result/transaction must raise
        InvalidAPIResponseError, not KeyError('result')."""
        api._sessions.post.return_value = MagicMock(
            json=lambda: {"responseHeader": {"responseCode": 0}}
        )
        with pytest.raises(InvalidAPIResponseError):
            api.check_action_status(token, vehicle, "t1")

    def test_missing_transaction_raises_invalid_api_response(self, api, token, vehicle):
        api._sessions.post.return_value = MagicMock(
            json=lambda: {
                "responseHeader": {"responseCode": 0},
                "result": {},
            }
        )
        with pytest.raises(InvalidAPIResponseError):
            api.check_action_status(token, vehicle, "t1")

    def test_pending(self, api, token, vehicle):
        api._sessions.post.return_value = MagicMock(
            json=lambda: {
                "responseHeader": {"responseCode": 0},
                "result": {"transaction": {"apiResult": "P", "apiStatusCode": "null"}},
            }
        )
        assert api.check_action_status(token, vehicle, "t1") is ORDER_STATUS.PENDING
