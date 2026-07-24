"""Tests for USA auth-chain error mapping in login / _verify_otp / _complete_login_with_otp.

KiaUvoApiUSA must raise typed exceptions (AuthenticationError / APIError) instead of
bare Exception, so Home Assistant can surface wrong-credentials / wrong-OTP via re-auth.
"""

import json

import pytest
from unittest.mock import MagicMock

from hyundai_kia_connect_api.ApiImpl import OTPRequest
from hyundai_kia_connect_api.KiaUvoApiUSA import KiaUvoApiUSA
from hyundai_kia_connect_api.Token import Token
from hyundai_kia_connect_api.exceptions import APIError, AuthenticationError


@pytest.fixture
def usa_api() -> KiaUvoApiUSA:
    api = KiaUvoApiUSA.__new__(KiaUvoApiUSA)
    api.API_URL = "https://example.com/"
    api._otp_handler = None
    api.api_headers = lambda: {}
    api.device_id = "device-key"
    return api


def _login_response(
    status_code: int,
    error_type: int = 0,
    error_code: int = 0,
    error_message: str = "",
    sid: str | None = None,
    payload: dict | None = None,
) -> MagicMock:
    """Build a mocked /prof/authUser response."""
    resp = MagicMock()
    body: dict = {
        "status": {
            "statusCode": status_code,
            "errorType": error_type,
            "errorCode": error_code,
            "errorMessage": error_message,
        }
    }
    if payload is not None:
        body["payload"] = payload
    resp.json.return_value = body
    resp.text = json.dumps(body)
    headers: dict = {}
    if sid is not None:
        headers["sid"] = sid
    resp.headers = headers
    return resp


def _otp_verify_response(
    status_code: int,
    error_message: str = "",
    sid: str | None = None,
    rmtoken: str | None = None,
) -> MagicMock:
    """Build a mocked /cmm/verifyOTP response."""
    resp = MagicMock()
    body = {
        "status": {
            "statusCode": status_code,
            "errorType": 0,
            "errorCode": 0,
            "errorMessage": error_message,
        }
    }
    resp.json.return_value = body
    resp.text = json.dumps(body)
    headers: dict = {}
    if sid is not None:
        headers["sid"] = sid
    if rmtoken is not None:
        headers["rmtoken"] = rmtoken
    resp.headers = headers
    return resp


def _complete_login_response(sid: str | None = None) -> MagicMock:
    """Build a mocked final /prof/authUser (complete-login) response."""
    resp = MagicMock()
    resp.json.return_value = {"status": {"statusCode": 0, "errorMessage": ""}}
    resp.text = "{}"
    resp.headers = {"sid": sid} if sid is not None else {}
    return resp


def test_login_wrong_credentials_raises_authentication_error(usa_api):
    token = Token(username="u", password="bad", access_token="x", refresh_token="r")
    usa_api.session = MagicMock()
    usa_api.session.post.return_value = _login_response(
        status_code=1,
        error_type=1,
        error_code=1001,
        error_message="Invalid Email or Password",
    )

    with pytest.raises(AuthenticationError) as exc_info:
        usa_api.login("u", "bad", token)
    assert "Invalid Email or Password" in str(exc_info.value)
    assert not isinstance(exc_info.value, APIError) or isinstance(
        exc_info.value, AuthenticationError
    )


def test_login_invalid_request_raises_apierror_not_auth(usa_api):
    """9789 'Invalid Request' is a protocol issue, not bad creds — must NOT be AuthenticationError."""
    token = Token(username="u", password="p", access_token="x", refresh_token="r")
    usa_api.session = MagicMock()
    usa_api.session.post.return_value = _login_response(
        status_code=1,
        error_type=3,
        error_code=9789,
        error_message="Invalid Request",
    )

    with pytest.raises(APIError) as exc_info:
        usa_api.login("u", "p", token)
    assert not isinstance(exc_info.value, AuthenticationError)


def test_login_status_zero_no_sid_raises_apierror(usa_api):
    token = Token(username="u", password="p", access_token="x", refresh_token="r")
    usa_api.session = MagicMock()
    usa_api.session.post.return_value = _login_response(
        status_code=0, error_message="Success with response body"
    )

    with pytest.raises(APIError) as exc_info:
        usa_api.login("u", "p", token)
    assert not isinstance(exc_info.value, AuthenticationError)


def test_login_success_returns_token(usa_api):
    """Regression: sid present -> Token (unchanged path)."""
    usa_api.session = MagicMock()
    usa_api.session.post.return_value = _login_response(
        status_code=0, sid="session-id", error_message="Success"
    )

    result = usa_api.login("u", "p")

    assert isinstance(result, Token)
    assert result.access_token == "session-id"


def test_login_otp_required_returns_otprequest(usa_api):
    """Regression: otpKey in payload -> OTPRequest (unchanged path)."""
    usa_api.session = MagicMock()
    usa_api.session.post.return_value = _login_response(
        status_code=0,
        payload={
            "otpKey": "k",
            "email": "e",
            "phone": "s",
            "hasEmail": True,
            "hasPhone": True,
        },
        error_message="Success",
    )

    result = usa_api.login("u", "p")

    assert isinstance(result, OTPRequest)
    assert result.otp_key == "k"


def test_verify_otp_wrong_code_raises_authentication_error(usa_api):
    usa_api.session = MagicMock()
    usa_api.session.post.return_value = _otp_verify_response(
        status_code=1, error_message="Invalid OTP"
    )

    with pytest.raises(AuthenticationError) as exc_info:
        usa_api._verify_otp("otpkey", "0000", "xid")
    assert "OTP verification failed" in str(exc_info.value)


def test_verify_otp_missing_sid_rmtoken_raises_apierror(usa_api):
    usa_api.session = MagicMock()
    usa_api.session.post.return_value = _otp_verify_response(status_code=0)

    with pytest.raises(APIError) as exc_info:
        usa_api._verify_otp("otpkey", "1234", "xid")
    assert not isinstance(exc_info.value, AuthenticationError)


def test_verify_otp_success_returns_sid_rmtoken(usa_api):
    usa_api.session = MagicMock()
    usa_api.session.post.return_value = _otp_verify_response(
        status_code=0, sid="sid-val", rmtoken="rm-val"
    )

    sid, rmtoken = usa_api._verify_otp("otpkey", "1234", "xid")

    assert sid == "sid-val"
    assert rmtoken == "rm-val"


def test_verify_otp_no_status_block_raises_apierror(usa_api):
    """A malformed verifyOTP response with no status block must not be misclassified as wrong-OTP (AuthenticationError)."""
    usa_api.session = MagicMock()
    resp = MagicMock()
    resp.json.return_value = {}  # no "status" key at all
    resp.text = "{}"
    resp.headers = {}
    usa_api.session.post.return_value = resp

    with pytest.raises(APIError) as exc_info:
        usa_api._verify_otp("otpkey", "1234", "xid")
    assert not isinstance(exc_info.value, AuthenticationError)


def test_complete_login_with_otp_missing_sid_raises_apierror(usa_api):
    usa_api.session = MagicMock()
    usa_api.session.post.return_value = _complete_login_response(sid=None)

    with pytest.raises(APIError) as exc_info:
        usa_api._complete_login_with_otp("u", "p", "sid", "rmtoken")
    assert "No final sid returned" in str(exc_info.value)
    assert not isinstance(exc_info.value, AuthenticationError)


def test_complete_login_with_otp_success_returns_sid(usa_api):
    usa_api.session = MagicMock()
    usa_api.session.post.return_value = _complete_login_response(sid="final-sid")

    result = usa_api._complete_login_with_otp("u", "p", "sid", "rmtoken")

    assert result == "final-sid"
