"""Tests for KiaUvoApiAU.login() token lifetime (kia_uvo #1778, NZ finding).

The AU/NZ gateway returns tokens with expires_in (e.g. 21600s = 6h), but
KiaUvoApiAU.login() hardcoded valid_until = now + 23h, so the library kept
using a dead token for ~17h and HA reauthed ~twice a day. login() must now
derive valid_until from the access_token response's expires_in, falling back
to LOGIN_TOKEN_LIFETIME (23h) only when the server omits it.
"""

import datetime as dt
from unittest.mock import MagicMock

from hyundai_kia_connect_api.KiaUvoApiAU import KiaUvoApiAU
from hyundai_kia_connect_api.const import LOGIN_TOKEN_LIFETIME


def _au_api_with_login_chain(expires_in: int | None) -> KiaUvoApiAU:
    """AU api with the full login chain stubbed; _get_access_token returns
    the given expires_in as the 4th element."""
    api = KiaUvoApiAU(region=5, brand=2, language="en")
    api._get_stamp = lambda: "S"  # type: ignore[assignment]
    api._get_device_id = lambda stamp: "dev"  # type: ignore[assignment]
    api._get_cookies = lambda: {}  # type: ignore[assignment]
    api._get_authorization_code_with_redirect_url = (  # type: ignore[assignment]
        lambda username, password, cookies: "authcode"
    )
    api._get_access_token = (  # type: ignore[assignment]
        lambda authorization_code, stamp: ("Bearer", "Bearer atk", "rtk", expires_in)
    )
    api._get_refresh_token = (  # type: ignore[assignment]
        lambda authorization_code, stamp: ("Bearer", "Bearer rtk")
    )
    return api


def test_au_get_access_token_returns_expires_in() -> None:
    """_get_access_token must surface the response expires_in (4th element)."""
    api = KiaUvoApiAU(region=5, brand=2, language="en")
    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "token_type": "Bearer",
        "access_token": "atk",
        "refresh_token": "rtk",
        "expires_in": 21600,
    }
    api.session.post = MagicMock(return_value=mock_resp)  # type: ignore[assignment]
    result = api._get_access_token("authcode", "S")
    assert result[3] == 21600


def test_au_login_valid_until_from_expires_in() -> None:
    """login() must set valid_until from expires_in (6h), not the 23h hardcode."""
    api = _au_api_with_login_chain(expires_in=21600)
    token = api.login("u", "p", pin="0000")
    delta = token.valid_until - dt.datetime.now(dt.timezone.utc)
    assert dt.timedelta(hours=5, minutes=55) < delta < dt.timedelta(hours=6, minutes=5)


def test_au_login_valid_until_fallback_when_no_expires_in() -> None:
    """When the server omits expires_in, fall back to LOGIN_TOKEN_LIFETIME (23h)."""
    api = _au_api_with_login_chain(expires_in=None)
    token = api.login("u", "p", pin="0000")
    delta = token.valid_until - dt.datetime.now(dt.timezone.utc)
    assert (
        dt.timedelta(hours=22, minutes=55) < delta < dt.timedelta(hours=23, minutes=5)
    )
    # sanity: the fallback is the existing constant, not some other value
    assert LOGIN_TOKEN_LIFETIME == dt.timedelta(hours=23)
