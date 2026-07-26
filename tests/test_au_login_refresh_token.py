"""Tests for KiaUvoApiAU.login() refresh-token storage (kia_uvo #1778, NZ finding).

login() previously minted a second access token via a refresh_token grant and
stored THAT, "Bearer "-prefixed, as Token.refresh_token — discarding the real
refresh token returned by the authorization_code grant. The AU/NZ gateway
rejects both deviations independently with 4002 "Invalid parameters"
("Bearer "-prefixed values, and access tokens in the refresh_token slot), so
refresh_access_token could never succeed and always fell back to a full login.
login() must store the authorization_code grant's refresh_token verbatim.
"""

from unittest.mock import MagicMock

from hyundai_kia_connect_api.KiaUvoApiAU import KiaUvoApiAU


def _au_api_with_login_chain() -> KiaUvoApiAU:
    """AU api with the full login chain stubbed at the helper level."""
    api = KiaUvoApiAU(region=5, brand=2, language="en")
    api._get_stamp = lambda: "S"  # type: ignore[assignment]
    api._get_device_id = lambda stamp: "dev"  # type: ignore[assignment]
    api._get_cookies = lambda: {}  # type: ignore[assignment]
    api._get_authorization_code_with_redirect_url = (  # type: ignore[assignment]
        lambda username, password, cookies: "authcode"
    )
    api._get_access_token = (  # type: ignore[assignment]
        lambda authorization_code, stamp: ("Bearer", "Bearer atk", "raw-rtk", 21600)
    )
    return api


def test_au_get_access_token_returns_raw_refresh_token() -> None:
    """_get_access_token must surface the response refresh_token verbatim
    (3rd element) — no "Bearer " prefix, not a second access token."""
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
    assert result[2] == "rtk"


def test_au_login_stores_real_refresh_token_verbatim() -> None:
    """login() must store the auth-code grant's refresh_token, unmodified."""
    api = _au_api_with_login_chain()
    token = api.login("u", "p", pin="0000")
    assert token.refresh_token == "raw-rtk"


def test_au_login_makes_no_second_token_request() -> None:
    """login() must not mint a second access token to use as refresh token.

    With every login helper stubbed, no request should reach the session at
    all — the old _get_refresh_token grant was the only remaining one.
    """
    api = _au_api_with_login_chain()
    api.session.post = MagicMock()  # type: ignore[assignment]
    api.login("u", "p", pin="0000")
    api.session.post.assert_not_called()
