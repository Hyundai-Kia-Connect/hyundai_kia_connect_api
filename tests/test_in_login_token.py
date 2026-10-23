"""Tests for KiaUvoApiIN.login() token handling.

login() used to mint a second access token via a refresh_token grant and
store THAT, "Bearer "-prefixed, as Token.refresh_token, discarding the real
refresh token from the authorization_code grant. The refresh grant then
always failed with 4002 "Invalid parameters" and fell back to a full login.
It also hardcoded valid_until = now + 23h while the IN gateway expires access
tokens after about an hour, so every poll for ~22h failed with
"Key not authorized: token has expired" (same bugs as AU, kia_uvo #1778).
"""

import datetime as dt
from unittest.mock import MagicMock

from hyundai_kia_connect_api.const import LOGIN_TOKEN_LIFETIME
from hyundai_kia_connect_api.KiaUvoApiIN import KiaUvoApiIN


def _in_api() -> KiaUvoApiIN:
    return KiaUvoApiIN(region=6, brand=1, language="en")


def _in_api_with_login_chain(expires_in: int | None) -> KiaUvoApiIN:
    """IN api with the full login chain stubbed at the helper level."""
    api = _in_api()
    api._get_stamp = lambda: "S"  # type: ignore[assignment]
    api._get_device_id = lambda stamp: "dev"  # type: ignore[assignment]
    api._get_cookies = dict  # type: ignore[assignment]
    api._get_authorization_code_with_redirect_url = (  # type: ignore[assignment]
        lambda username, password, cookies: "authcode"
    )
    api._get_access_token = (  # type: ignore[assignment]
        lambda stamp, authorization_code: (
            "Bearer",
            "Bearer atk",
            "raw-rtk",
            expires_in,
        )
    )
    return api


def _json_response(payload: dict) -> MagicMock:
    resp = MagicMock()
    resp.json.return_value = payload
    return resp


def test_in_get_access_token_returns_raw_refresh_token_and_expires_in() -> None:
    api = _in_api()
    api.session.post = MagicMock(  # type: ignore[assignment]
        return_value=_json_response(
            {
                "token_type": "Bearer",
                "access_token": "atk",
                "refresh_token": "rtk",
                "expires_in": 3600,
            }
        )
    )
    token_type, access_token, refresh_token, expires_in = api._get_access_token(
        "S", "authcode"
    )
    assert token_type == "Bearer"
    assert access_token == "Bearer atk"
    assert refresh_token == "rtk"
    assert expires_in == 3600


def test_in_login_stores_real_refresh_token_verbatim() -> None:
    token = _in_api_with_login_chain(expires_in=3600).login("u", "p", pin="0000")
    assert token.refresh_token == "raw-rtk"
    assert token.access_token == "Bearer atk"


def test_in_login_makes_no_second_token_request() -> None:
    api = _in_api_with_login_chain(expires_in=3600)
    api.session.post = MagicMock()  # type: ignore[assignment]
    api.login("u", "p", pin="0000")
    api.session.post.assert_not_called()


def test_in_login_valid_until_from_expires_in() -> None:
    token = _in_api_with_login_chain(expires_in=3600).login("u", "p", pin="0000")
    delta = token.valid_until - dt.datetime.now(dt.UTC)
    assert dt.timedelta(minutes=55) < delta <= dt.timedelta(hours=1)


def test_in_login_valid_until_fallback_when_no_expires_in() -> None:
    token = _in_api_with_login_chain(expires_in=None).login("u", "p", pin="0000")
    delta = token.valid_until - dt.datetime.now(dt.UTC)
    assert LOGIN_TOKEN_LIFETIME - dt.timedelta(minutes=5) < delta
    assert delta <= LOGIN_TOKEN_LIFETIME
