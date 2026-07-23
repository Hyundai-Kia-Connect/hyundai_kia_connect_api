"""Tests for USA auth-chain error mapping in login / _verify_otp / _complete_login_with_otp.

KiaUvoApiUSA must raise typed exceptions (AuthenticationError / APIError) instead of
bare Exception, so Home Assistant can surface wrong-credentials / wrong-OTP via re-auth.
"""

import json

import pytest
from unittest.mock import MagicMock

from hyundai_kia_connect_api.KiaUvoApiUSA import KiaUvoApiUSA


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
