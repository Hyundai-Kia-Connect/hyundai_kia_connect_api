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


def test_get_vehicles_recovers_after_relogin(usa_api):
    token = _token()
    usa_api._session = MagicMock()
    usa_api._session.get.side_effect = [_expired_response(), _vehicles_response()]
    usa_api.login = MagicMock(return_value=_fresh_token())

    result = usa_api.get_vehicles(token)

    assert result == []
    assert usa_api._session.get.call_count == 2
    usa_api.login.assert_called_once()
    assert token.access_token == "fresh-sid"
    assert token.refresh_token == "rm2"


def test_get_vehicles_success_no_retry(usa_api):
    token = _token()
    usa_api._session = MagicMock()
    usa_api._session.get.return_value = _vehicles_response()
    usa_api.login = MagicMock()

    result = usa_api.get_vehicles(token)

    assert result == []
    usa_api.login.assert_not_called()
    assert usa_api._session.get.call_count == 1


def test_get_vehicles_raises_otp_when_login_needs_otp(usa_api):
    token = _token()
    usa_api._session = MagicMock()
    usa_api._session.get.return_value = _expired_response()
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
        usa_api.get_vehicles(token)


def test_get_vehicles_one_retry_only(usa_api):
    token = _token()
    usa_api._session = MagicMock()
    usa_api._session.get.side_effect = [_expired_response(), _expired_response()]
    usa_api.login = MagicMock(return_value=_fresh_token())

    with pytest.raises(AuthenticationError):
        usa_api.get_vehicles(token)
    assert usa_api._session.get.call_count == 2
    usa_api.login.assert_called_once()


def test_refresh_vehicles_recovers_after_relogin(usa_api):
    token = _token()
    usa_api._session = MagicMock()
    usa_api._session.get.side_effect = [_expired_response(), _vehicles_response()]
    usa_api.login = MagicMock(return_value=_fresh_token())

    # Pass an empty list — refresh_vehicles iterates payload.vehicleSummary
    usa_api.refresh_vehicles(token, [])

    assert usa_api._session.get.call_count == 2
    usa_api.login.assert_called_once()
    assert token.access_token == "fresh-sid"
    assert token.refresh_token == "rm2"


def test_refresh_vehicles_success_no_retry(usa_api):
    token = _token()
    usa_api._session = MagicMock()
    usa_api._session.get.return_value = _vehicles_response()
    usa_api.login = MagicMock()

    usa_api.refresh_vehicles(token, [])

    usa_api.login.assert_not_called()
    assert usa_api._session.get.call_count == 1


def test_refresh_vehicles_one_retry_only(usa_api):
    token = _token()
    usa_api._session = MagicMock()
    usa_api._session.get.side_effect = [_expired_response(), _expired_response()]
    usa_api.login = MagicMock(return_value=_fresh_token())

    with pytest.raises(AuthenticationError):
        usa_api.refresh_vehicles(token, [])
    assert usa_api._session.get.call_count == 2
    usa_api.login.assert_called_once()
