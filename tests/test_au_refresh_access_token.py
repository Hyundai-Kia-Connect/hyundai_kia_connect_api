"""Tests for the refresh_access_token Stamp hook (kia_uvo #1778, AU)."""

from unittest.mock import MagicMock

from hyundai_kia_connect_api.KiaUvoApiAU import KiaUvoApiAU
from hyundai_kia_connect_api.KiaUvoApiCN import KiaUvoApiCN
from hyundai_kia_connect_api.KiaUvoApiIN import KiaUvoApiIN
from hyundai_kia_connect_api.Token import Token

_TOKEN_RESPONSE = {
    "token_type": "Bearer",
    "access_token": "atk",
    "refresh_token": "rtk",
    "expires_in": 86400,
}


def test_cn_refresh_hook_default_empty() -> None:
    """CN inherits the base hook and must not send Stamp on refresh."""
    api = KiaUvoApiCN(region=4, brand=1, language="en")
    assert api._refresh_access_token_headers() == {}


def test_in_refresh_hook_default_empty() -> None:
    """IN inherits the base hook and must not send Stamp on refresh."""
    api = KiaUvoApiIN(brand=2)
    assert api._refresh_access_token_headers() == {}


def _au_api_with_mock_session() -> KiaUvoApiAU:
    api = KiaUvoApiAU(region=5, brand=2, language="en")
    api._get_stamp = lambda: "STAMP-FAKE"  # type: ignore[assignment]
    mock_resp = MagicMock()
    mock_resp.json.return_value = _TOKEN_RESPONSE
    api.session.post = MagicMock(return_value=mock_resp)  # type: ignore[assignment]
    return api


def test_au_refresh_sends_stamp() -> None:
    """AU refresh_token grant must include the Stamp header."""
    api = _au_api_with_mock_session()
    api.refresh_access_token(Token(refresh_token="old-rtk"))
    sent_headers = api.session.post.call_args.kwargs["headers"]
    assert sent_headers.get("Stamp") == "STAMP-FAKE"


def test_au_refresh_grant_type_and_url() -> None:
    """AU refresh uses grant_type=refresh_token to the oauth2/token endpoint."""
    api = _au_api_with_mock_session()
    api.refresh_access_token(Token(refresh_token="old-rtk"))
    call = api.session.post.call_args
    assert call.args[0] == api.USER_API_URL + "oauth2/token"
    assert "grant_type=refresh_token" in call.kwargs["data"]
    assert "refresh_token=old-rtk" in call.kwargs["data"]
