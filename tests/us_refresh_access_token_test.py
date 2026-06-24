"""Tests for HyundaiBlueLinkApiUSA.refresh_access_token().

The USA /v2/ac/oauth/token endpoint honors grant_type=refresh_token only when
username + password + grant_type + refresh_token are sent together in a JSON
body (the endpoint validates username/password presence before inspecting
grant_type). These tests lock in that contract and the fallback-to-login
behaviour. Confirmed live 2026-06-22: ~0.02s refresh vs ~0.74s full login,
refresh_token rotates, expires_in ~1800s.
"""

import datetime as dt
from unittest.mock import MagicMock, patch

from hyundai_kia_connect_api.HyundaiBlueLinkApiUSA import HyundaiBlueLinkApiUSA
from hyundai_kia_connect_api.Token import Token


def _make_usa_api() -> HyundaiBlueLinkApiUSA:
    """Create a HyundaiBlueLinkApiUSA instance for testing (no network)."""
    return HyundaiBlueLinkApiUSA(region=3, brand=2, language="en")


def _make_token(**overrides) -> Token:
    """Create a Token instance with sensible defaults for testing."""
    defaults = dict(
        username="user@test.com",
        password="MyPassword123!",
        access_token="old-access-token",
        refresh_token="OLDREFRESHTOKEN1234567890123456789012345678",
        valid_until=dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=1),
        pin="1234",
    )
    defaults.update(overrides)
    return Token(**defaults)


def _mock_refresh_response(
    access_token: str = "new-access-token",
    refresh_token: str | None = "new-refresh-token",
    expires_in: str = "1799",
) -> MagicMock:
    """Build a mock response object for a successful refresh POST."""
    body = {"access_token": access_token, "expires_in": expires_in}
    if refresh_token is not None:
        body["refresh_token"] = refresh_token
    mock_resp = MagicMock()
    mock_resp.json.return_value = body
    return mock_resp


def test_refresh_access_token_uses_stored_refresh_token() -> None:
    """refresh_access_token returns a new Token with rotated credentials."""
    api = _make_usa_api()

    with patch.object(
        api.session, "post", return_value=_mock_refresh_response()
    ) as mock_post:
        token = _make_token()
        result = api.refresh_access_token(token)

    assert result.access_token == "new-access-token"
    assert result.refresh_token == "new-refresh-token"
    assert result.username == "user@test.com"
    assert result.password == "MyPassword123!"
    assert result.pin == "1234"
    mock_post.assert_called_once()


def test_refresh_access_token_sends_grant_type_and_credentials() -> None:
    """The POST body must include username, password, grant_type, refresh_token.

    This is the key contract: the USA endpoint validates username/password
    presence before honoring grant_type, so all four fields must be sent
    together (confirmed live in issue #1186 round-2 diagnostic, T1).
    """
    api = _make_usa_api()

    with patch.object(
        api.session, "post", return_value=_mock_refresh_response()
    ) as mock_post:
        token = _make_token()
        api.refresh_access_token(token)

    call = mock_post.call_args
    url = call.args[0]
    body = call.kwargs["json"]
    assert url == api.LOGIN_API + "oauth/token"
    assert body["username"] == "user@test.com"
    assert body["password"] == "MyPassword123!"
    assert body["grant_type"] == "refresh_token"
    assert body["refresh_token"] == token.refresh_token


def test_refresh_access_token_rotates_refresh_token() -> None:
    """A rotated refresh_token in the response replaces the old one."""
    api = _make_usa_api()

    with patch.object(
        api.session,
        "post",
        return_value=_mock_refresh_response(refresh_token="ROTATED"),
    ):
        token = _make_token(refresh_token="ORIGINAL")
        result = api.refresh_access_token(token)

    assert result.refresh_token == "ROTATED"


def test_refresh_access_token_keeps_old_refresh_token_when_missing() -> None:
    """When the response omits refresh_token, keep the original (no rotation)."""
    api = _make_usa_api()

    with patch.object(
        api.session, "post", return_value=_mock_refresh_response(refresh_token=None)
    ):
        token = _make_token(refresh_token="ORIGINAL")
        result = api.refresh_access_token(token)

    assert result.access_token == "new-access-token"
    assert result.refresh_token == "ORIGINAL"


def test_refresh_access_token_sets_valid_until_from_expires_in() -> None:
    """valid_until is now + expires_in seconds."""
    api = _make_usa_api()

    with patch.object(
        api.session, "post", return_value=_mock_refresh_response(expires_in="3600")
    ):
        before = dt.datetime.now(dt.timezone.utc)
        token = _make_token()
        result = api.refresh_access_token(token)
        after = dt.datetime.now(dt.timezone.utc)

    assert result.valid_until >= before + dt.timedelta(seconds=3599)
    assert result.valid_until <= after + dt.timedelta(seconds=3601)


def test_refresh_access_token_falls_back_on_missing_refresh_token() -> None:
    """When token has no refresh_token, fall back to full login."""
    api = _make_usa_api()

    with (
        patch.object(
            api, "login", return_value=_make_token(access_token="from-login")
        ) as mock_login,
        patch.object(api.session, "post") as mock_post,
    ):
        token = _make_token(refresh_token="")
        result = api.refresh_access_token(token)

    mock_login.assert_called_once_with("user@test.com", "MyPassword123!", "1234")
    mock_post.assert_not_called()
    assert result.access_token == "from-login"


def test_refresh_access_token_falls_back_on_none_refresh_token() -> None:
    """When token.refresh_token is None, fall back to full login."""
    api = _make_usa_api()

    with (
        patch.object(
            api, "login", return_value=_make_token(access_token="from-login")
        ) as mock_login,
        patch.object(api.session, "post") as mock_post,
    ):
        token = _make_token(refresh_token=None)
        api.refresh_access_token(token)

    mock_login.assert_called_once_with("user@test.com", "MyPassword123!", "1234")
    mock_post.assert_not_called()


def test_refresh_access_token_falls_back_on_exchange_failure() -> None:
    """When the refresh POST raises, fall back to full login."""
    api = _make_usa_api()

    with (
        patch.object(api.session, "post", side_effect=Exception("Network error")),
        patch.object(
            api, "login", return_value=_make_token(access_token="from-login")
        ) as mock_login,
    ):
        token = _make_token()
        result = api.refresh_access_token(token)

    mock_login.assert_called_once_with("user@test.com", "MyPassword123!", "1234")
    assert result.access_token == "from-login"


def test_refresh_access_token_falls_back_on_error_response() -> None:
    """When the response carries an errorCode, fall back to full login.

    _check_response_for_errors raises APIError when errorCode is present;
    refresh_access_token must catch that and fall back rather than propagate.
    """
    api = _make_usa_api()

    error_resp = MagicMock()
    error_resp.json.return_value = {
        "errorCode": "400",
        "errorMessage": "refresh rejected",
    }

    with (
        patch.object(api.session, "post", return_value=error_resp),
        patch.object(
            api, "login", return_value=_make_token(access_token="from-login")
        ) as mock_login,
    ):
        token = _make_token()
        result = api.refresh_access_token(token)

    mock_login.assert_called_once_with("user@test.com", "MyPassword123!", "1234")
    assert result.access_token == "from-login"


def test_refresh_access_token_does_not_call_login_on_success() -> None:
    """On a successful refresh, the full login path is not taken."""
    api = _make_usa_api()

    with (
        patch.object(api.session, "post", return_value=_mock_refresh_response()),
        patch.object(api, "login") as mock_login,
    ):
        token = _make_token()
        api.refresh_access_token(token)

    mock_login.assert_not_called()
