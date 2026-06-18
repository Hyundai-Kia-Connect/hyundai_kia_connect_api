"""Tests for USA session-expiry auto-recovery in get_vehicles / refresh_vehicles."""

import pytest
from unittest.mock import MagicMock

from hyundai_kia_connect_api.ApiImpl import OTPRequest
from hyundai_kia_connect_api.KiaUvoApiUSA import KiaUvoApiUSA, _retry_on_auth_error
from hyundai_kia_connect_api.Token import Token
from hyundai_kia_connect_api.exceptions import (
    AuthenticationError,
    AuthenticationOTPRequired,
)


@pytest.fixture
def usa_api() -> KiaUvoApiUSA:
    api = KiaUvoApiUSA.__new__(KiaUvoApiUSA)
    api.API_URL = "https://example.com/"
    api._otp_handler = None
    api.api_headers = lambda: {}
    return api


def _token() -> Token:
    return Token(
        username="user@example.com",
        password="pass",
        access_token="dead-sid",
        refresh_token="rm",
    )


def _fresh_token() -> Token:
    return Token(
        username="user@example.com",
        password="pass",
        access_token="fresh-sid",
        refresh_token="rm2",
    )


def _expired_response() -> MagicMock:
    resp = MagicMock()
    resp.json.return_value = {
        "status": {
            "statusCode": 1,
            "errorType": 1,
            "errorCode": 1003,
            "errorMessage": "Session Key is either invalid or expired",
        }
    }
    resp.text = "{}"
    return resp


def _vehicles_response() -> MagicMock:
    resp = MagicMock()
    resp.json.return_value = {
        "status": {"statusCode": 0},
        "payload": {"vehicleSummary": []},
    }
    resp.text = "{}"
    return resp


def test_decorator_retries_once_and_mutates_token(usa_api):
    calls = {"n": 0}

    @_retry_on_auth_error
    def do_thing(self, token):
        calls["n"] += 1
        if calls["n"] == 1:
            raise AuthenticationError("expired")
        return "ok"

    token = _token()
    usa_api.login = MagicMock(return_value=_fresh_token())

    result = do_thing(usa_api, token)

    assert result == "ok"
    assert calls["n"] == 2
    usa_api.login.assert_called_once()
    assert token.access_token == "fresh-sid"
    assert token.refresh_token == "rm2"


def test_decorator_raises_otp_when_login_needs_otp(usa_api):
    @_retry_on_auth_error
    def do_thing(self, token):
        raise AuthenticationError("expired")

    token = _token()
    otp = OTPRequest(
        otp_key="k",
        request_id="r",
        email="e",
        sms="s",
        has_email=True,
        has_sms=False,
    )
    usa_api.login = MagicMock(return_value=otp)

    with pytest.raises(AuthenticationOTPRequired):
        do_thing(usa_api, token)


def test_decorator_one_retry_only(usa_api):
    calls = {"n": 0}

    @_retry_on_auth_error
    def do_thing(self, token):
        calls["n"] += 1
        raise AuthenticationError("expired")

    token = _token()
    usa_api.login = MagicMock(return_value=_fresh_token())

    with pytest.raises(AuthenticationError):
        do_thing(usa_api, token)
    assert calls["n"] == 2
    usa_api.login.assert_called_once()
