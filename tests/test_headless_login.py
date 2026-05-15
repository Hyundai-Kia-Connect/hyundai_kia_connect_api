"""Tests for KiaUvoApiEU._login_with_password(), login() flow routing, and refresh_access_token()."""

import datetime as dt
from contextlib import ExitStack
from unittest.mock import MagicMock, patch

import pytest

from hyundai_kia_connect_api.KiaUvoApiEU import KiaUvoApiEU
from hyundai_kia_connect_api.Token import Token
from hyundai_kia_connect_api.exceptions import AuthenticationError, ConsentRequiredError


# ── Helper: patches for _login_with_password() tests ─────────────
# RSA/PKCS1v15 crypto is not under test, so we mock it out.


def _mock_crypto():
    """Return patches for RSA.construct and PKCS1_v1_5.new."""
    mock_cipher = MagicMock()
    mock_cipher.encrypt.return_value = b"\x00" * 256  # fake encrypted password
    return [
        patch("hyundai_kia_connect_api.KiaUvoApiEU.RSA.construct"),
        patch(
            "hyundai_kia_connect_api.KiaUvoApiEU.PKCS1_v1_5.new",
            return_value=mock_cipher,
        ),
    ]


def _make_eu_api(brand: int = 1) -> KiaUvoApiEU:
    """Create a KiaUvoApiEU instance for testing."""
    return KiaUvoApiEU(region=1, brand=brand, language="en")


# ── _login_with_password() error paths ──────────────────────────


def test_login_with_password_certs_endpoint_fails():
    """Certs endpoint returns non-200 -> AuthenticationError."""
    api = _make_eu_api(brand=1)

    mock_response = MagicMock()
    mock_response.status_code = 500

    mock_session = MagicMock()
    mock_session.get.return_value = mock_response

    with ExitStack() as stack:
        stack.enter_context(
            patch(
                "hyundai_kia_connect_api.KiaUvoApiEU.requests.Session",
                return_value=mock_session,
            )
        )
        for p in _mock_crypto():
            stack.enter_context(p)
        with pytest.raises(AuthenticationError, match="failed to fetch RSA certs"):
            api._login_with_password("user@test.com", "password")


def test_login_with_password_signin_returns_non_302():
    """Signin endpoint returns 200 instead of 302 -> AuthenticationError."""
    api = _make_eu_api(brand=1)

    certs_resp = MagicMock()
    certs_resp.status_code = 200
    certs_resp.json.return_value = {
        "retValue": {
            "kid": "test-kid",
            "n": "AJRQISPa0AJRQISPa0AJRQISPa0AJRQISPa0AJRQISPa0A",
            "e": "AQAB",
        }
    }

    signin_resp = MagicMock()
    signin_resp.status_code = 200
    signin_resp.text = "Login page"

    mock_session = MagicMock()
    mock_session.get.return_value = certs_resp
    mock_session.post.return_value = signin_resp

    with ExitStack() as stack:
        stack.enter_context(
            patch(
                "hyundai_kia_connect_api.KiaUvoApiEU.requests.Session",
                return_value=mock_session,
            )
        )
        for p in _mock_crypto():
            stack.enter_context(p)
        with pytest.raises(AuthenticationError, match="Signin failed"):
            api._login_with_password("user@test.com", "password")


def test_login_with_password_signin_no_code_in_redirect():
    """Signin redirect has no code parameter -> AuthenticationError."""
    api = _make_eu_api(brand=1)

    certs_resp = MagicMock()
    certs_resp.status_code = 200
    certs_resp.json.return_value = {
        "retValue": {
            "kid": "test-kid",
            "n": "AJRQISPa0AJRQISPa0AJRQISPa0AJRQISPa0AJRQISPa0A",
            "e": "AQAB",
        }
    }

    signin_resp = MagicMock()
    signin_resp.status_code = 302
    signin_resp.headers = {"location": "https://example.com/login?no_code=true"}

    mock_session = MagicMock()
    mock_session.get.return_value = certs_resp
    mock_session.post.return_value = signin_resp

    with ExitStack() as stack:
        stack.enter_context(
            patch(
                "hyundai_kia_connect_api.KiaUvoApiEU.requests.Session",
                return_value=mock_session,
            )
        )
        for p in _mock_crypto():
            stack.enter_context(p)
        with pytest.raises(
            AuthenticationError, match="unexpected redirect after signin"
        ):
            api._login_with_password("user@test.com", "password")


def test_login_with_password_signin_error_in_redirect():
    """Signin redirect contains error parameter -> AuthenticationError."""
    api = _make_eu_api(brand=1)

    certs_resp = MagicMock()
    certs_resp.status_code = 200
    certs_resp.json.return_value = {
        "retValue": {
            "kid": "test-kid",
            "n": "AJRQISPa0AJRQISPa0AJRQISPa0AJRQISPa0AJRQISPa0A",
            "e": "AQAB",
        }
    }

    signin_resp = MagicMock()
    signin_resp.status_code = 302
    signin_resp.headers = {
        "location": (
            "https://example.com/callback?error=access_denied"
            "&error_description=Invalid+credentials"
        )
    }

    mock_session = MagicMock()
    mock_session.get.return_value = certs_resp
    mock_session.post.return_value = signin_resp

    with ExitStack() as stack:
        stack.enter_context(
            patch(
                "hyundai_kia_connect_api.KiaUvoApiEU.requests.Session",
                return_value=mock_session,
            )
        )
        for p in _mock_crypto():
            stack.enter_context(p)
        with pytest.raises(AuthenticationError, match="Authentication rejected"):
            api._login_with_password("user@test.com", "wrong-password")


def test_login_with_password_signin_redirect_to_login_page():
    """Signin redirects back to authorize page -> AuthenticationError."""
    api = _make_eu_api(brand=1)

    certs_resp = MagicMock()
    certs_resp.status_code = 200
    certs_resp.json.return_value = {
        "retValue": {
            "kid": "test-kid",
            "n": "AJRQISPa0AJRQISPa0AJRQISPa0AJRQISPa0AJRQISPa0A",
            "e": "AQAB",
        }
    }

    signin_resp = MagicMock()
    signin_resp.status_code = 302
    signin_resp.headers = {
        "location": (
            "https://idpconnect-eu.kia.com/auth/api/v2/user/oauth2/authorize?state=ccsp"
        )
    }

    mock_session = MagicMock()
    mock_session.get.return_value = certs_resp
    mock_session.post.return_value = signin_resp

    with ExitStack() as stack:
        stack.enter_context(
            patch(
                "hyundai_kia_connect_api.KiaUvoApiEU.requests.Session",
                return_value=mock_session,
            )
        )
        for p in _mock_crypto():
            stack.enter_context(p)
        with pytest.raises(AuthenticationError, match="returned to login page"):
            api._login_with_password("user@test.com", "password")


def test_login_with_password_signin_consent_spa_redirect():
    """Kia EU redirects to /web/v1/user/authorization SPA (consent page)."""
    api = _make_eu_api(brand=1)

    certs_resp = MagicMock()
    certs_resp.status_code = 200
    certs_resp.json.return_value = {
        "retValue": {
            "kid": "test-kid",
            "n": "AJRQISPa0AJRQISPa0AJRQISPa0AJRQISPa0AJRQISPa0A",
            "e": "AQAB",
        }
    }

    signin_resp = MagicMock()
    signin_resp.status_code = 302
    signin_resp.headers = {
        "location": "https://prd.eu-ccapi.kia.com:8080/web/v1/user/authorization"
    }

    mock_session = MagicMock()
    mock_session.get.return_value = certs_resp
    mock_session.post.return_value = signin_resp

    with ExitStack() as stack:
        stack.enter_context(
            patch(
                "hyundai_kia_connect_api.KiaUvoApiEU.requests.Session",
                return_value=mock_session,
            )
        )
        for p in _mock_crypto():
            stack.enter_context(p)
        with pytest.raises(ConsentRequiredError, match="consent is required"):
            api._login_with_password("user@test.com", "password")


def test_login_with_password_token_exchange_fails():
    """Token exchange returns non-200 -> AuthenticationError."""
    api = _make_eu_api(brand=1)

    certs_resp = MagicMock()
    certs_resp.status_code = 200
    certs_resp.json.return_value = {
        "retValue": {
            "kid": "test-kid",
            "n": "AJRQISPa0AJRQISPa0AJRQISPa0AJRQISPa0AJRQISPa0A",
            "e": "AQAB",
        }
    }

    signin_resp = MagicMock()
    signin_resp.status_code = 302
    signin_resp.headers = {"location": "https://example.com/callback?code=abc123"}

    token_resp = MagicMock()
    token_resp.status_code = 400
    token_resp.text = "Bad request"

    mock_session = MagicMock()
    mock_session.get.return_value = certs_resp
    mock_session.post.return_value = signin_resp

    with ExitStack() as stack:
        stack.enter_context(
            patch(
                "hyundai_kia_connect_api.KiaUvoApiEU.requests.Session",
                return_value=mock_session,
            )
        )
        stack.enter_context(
            patch(
                "hyundai_kia_connect_api.KiaUvoApiEU.requests.post",
                return_value=token_resp,
            )
        )
        for p in _mock_crypto():
            stack.enter_context(p)
        with pytest.raises(AuthenticationError, match="token exchange failed"):
            api._login_with_password("user@test.com", "password")


def test_login_with_password_success():
    """Full successful _login_with_password flow returns correct tokens."""
    api = _make_eu_api(brand=2)  # Hyundai

    certs_resp = MagicMock()
    certs_resp.status_code = 200
    certs_resp.json.return_value = {
        "retValue": {
            "kid": "test-kid",
            "n": "AJRQISPa0AJRQISPa0AJRQISPa0AJRQISPa0AJRQISPa0A",
            "e": "AQAB",
        }
    }

    signin_resp = MagicMock()
    signin_resp.status_code = 302
    signin_resp.headers = {"location": "https://example.com/callback?code=abc123"}

    token_resp = MagicMock()
    token_resp.status_code = 200
    token_resp.json.return_value = {
        "token_type": "Bearer",
        "access_token": "test-access-token",
        "refresh_token": "TESTRFTOKEN12345678901234567890123456789012345678",
        "expires_in": 86400,
    }

    mock_session = MagicMock()
    mock_session.get.return_value = certs_resp
    mock_session.post.return_value = signin_resp

    with ExitStack() as stack:
        stack.enter_context(
            patch(
                "hyundai_kia_connect_api.KiaUvoApiEU.requests.Session",
                return_value=mock_session,
            )
        )
        stack.enter_context(
            patch(
                "hyundai_kia_connect_api.KiaUvoApiEU.requests.post",
                return_value=token_resp,
            )
        )
        for p in _mock_crypto():
            stack.enter_context(p)
        access_token, refresh_token, expires_in = api._login_with_password(
            "user@test.com", "password"
        )

    assert access_token == "Bearer test-access-token"
    assert refresh_token == "TESTRFTOKEN12345678901234567890123456789012345678"
    assert expires_in == 86400


# ── KiaUvoApiEU.login() flow routing ────────────────────────


def test_login_refresh_token_flow():
    """When password matches 48-char refresh_token, use _get_access_token()."""
    api = _make_eu_api(brand=1)  # Kia
    refresh_token = "NWIXYJNKZJMTZJE3MI01ZWI4LWI0NWETZJQ0NJI1OTFMOTC3"

    with (
        patch.object(api, "_get_stamp", return_value="stamp"),
        patch.object(api, "_get_device_id", return_value="device-123"),
        patch.object(api, "_get_cookies", return_value={}),
        patch.object(api, "_set_session_language"),
        patch.object(
            api,
            "_get_access_token",
            return_value=("Bearer", "Bearer access-token", "auth-code", 86400),
        ),
    ):
        token = api.login("user@test.com", refresh_token, pin="1234")

    assert token.access_token == "Bearer access-token"
    assert token.refresh_token == refresh_token
    assert token.device_id == "device-123"
    assert token.pin == "1234"


def test_login_plaintext_password_calls_login_with_password():
    """Plaintext password for Kia invokes _login_with_password()."""
    api = _make_eu_api(brand=1)  # Kia

    with (
        patch.object(api, "_get_stamp", return_value="stamp"),
        patch.object(api, "_get_device_id", return_value="device-123"),
        patch.object(api, "_get_cookies", return_value={}),
        patch.object(api, "_set_session_language"),
        patch.object(
            api,
            "_login_with_password",
            return_value=(
                "Bearer headless-access-token",
                "HEADLESSREFRESHTOKEN123456789012345678",
                3600,
            ),
        ),
    ):
        token = api.login("user@test.com", "MyPassword123!", pin="1234")

    assert token.access_token == "Bearer headless-access-token"
    assert token.refresh_token == "HEADLESSREFRESHTOKEN123456789012345678"
    assert token.pin == "1234"


def test_login_plaintext_password_genesis_calls_login_with_password():
    """Plaintext password for Genesis invokes _login_with_password()."""
    api = _make_eu_api(brand=3)  # Genesis

    with (
        patch.object(api, "_get_stamp", return_value="stamp"),
        patch.object(api, "_get_device_id", return_value="device-123"),
        patch.object(api, "_get_cookies", return_value={}),
        patch.object(api, "_set_session_language"),
        patch.object(
            api,
            "_login_with_password",
            return_value=(
                "Bearer genesis-access-token",
                "GENESISREFRESHTOKEN12345678901234567",
                3600,
            ),
        ),
    ):
        token = api.login("user@test.com", "MyPassword123!", pin="1234")

    assert token.access_token == "Bearer genesis-access-token"
    assert token.refresh_token == "GENESISREFRESHTOKEN12345678901234567"
    assert token.pin == "1234"


def test_login_genesis_password_fails_falls_back_to_error():
    """If _login_with_password fails for Genesis, error propagates."""
    api = _make_eu_api(brand=3)  # Genesis

    with (
        patch.object(api, "_get_stamp", return_value="stamp"),
        patch.object(api, "_get_device_id", return_value="device-123"),
        patch.object(api, "_get_cookies", return_value={}),
        patch.object(api, "_set_session_language"),
        patch.object(
            api,
            "_login_with_password",
            side_effect=AuthenticationError("Signin failed: HTTP 404"),
        ),
    ):
        with pytest.raises(AuthenticationError, match="Signin failed"):
            api.login("user@test.com", "MyPassword123!")


# ── refresh_access_token() tests ───────────────────────────────


def _make_token(**overrides) -> Token:
    """Create a Token instance with sensible defaults for testing."""
    defaults = dict(
        username="user@test.com",
        password="MyPassword123!",
        access_token="Bearer old-access-token",
        refresh_token="OLDREFRESHTOKEN1234567890123456789012345678",
        device_id="device-123",
        valid_until=dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=1),
        pin="1234",
    )
    defaults.update(overrides)
    return Token(**defaults)


def test_refresh_access_token_uses_stored_refresh_token():
    """refresh_access_token calls _get_access_token with stored refresh_token."""
    api = _make_eu_api(brand=1)  # Kia

    mock_get_token = MagicMock(
        return_value=("Bearer", "Bearer new-access-token", "new-rt", 86400)
    )
    with (
        patch.object(api, "_get_stamp", return_value="stamp"),
        patch.object(api, "_get_access_token", mock_get_token),
    ):
        token = _make_token()
        result = api.refresh_access_token(token)

    assert result.access_token == "Bearer new-access-token"
    assert result.refresh_token == "new-rt"
    assert result.device_id == "device-123"
    assert result.username == "user@test.com"
    assert result.pin == "1234"
    mock_get_token.assert_called_once_with("stamp", token.refresh_token)


def test_refresh_access_token_preserves_device_id():
    """refresh_access_token preserves device_id from original token."""
    api = _make_eu_api(brand=1)

    with (
        patch.object(api, "_get_stamp", return_value="stamp"),
        patch.object(
            api,
            "_get_access_token",
            return_value=("Bearer", "Bearer new-access-token", "rotated-rt", 3600),
        ),
    ):
        token = _make_token(device_id="original-device-id")
        result = api.refresh_access_token(token)

    assert result.device_id == "original-device-id"


def test_refresh_access_token_genesis_keeps_old_refresh_token():
    """When _get_access_token returns None for refresh_token, keep the original.

    Some brands (Genesis, Hyundai EU) don't rotate refresh tokens,
    so the API doesn't return a new one. The `or` fallback preserves
    the original refresh_token.
    """
    api = _make_eu_api(brand=3)  # Genesis

    with (
        patch.object(api, "_get_stamp", return_value="stamp"),
        patch.object(
            api,
            "_get_access_token",
            return_value=("Bearer", "Bearer gen-access", None, 3600),
        ),
    ):
        token = _make_token(refresh_token="GENESISRT1234567890123456789012345")
        result = api.refresh_access_token(token)

    assert result.access_token == "Bearer gen-access"
    # None from API → fallback to original refresh_token
    assert result.refresh_token == "GENESISRT1234567890123456789012345"
    assert result.device_id == "device-123"


def test_refresh_access_token_falls_back_on_missing_refresh_token():
    """When token has no refresh_token, fall back to full login."""
    api = _make_eu_api(brand=1)

    with patch.object(
        api, "login", return_value=_make_token(access_token="Bearer from-login")
    ) as mock_login:
        token = _make_token(refresh_token="")
        result = api.refresh_access_token(token)

    mock_login.assert_called_once_with("user@test.com", "MyPassword123!", "1234")
    assert result.access_token == "Bearer from-login"


def test_refresh_access_token_falls_back_on_none_refresh_token():
    """When token.refresh_token is None, fall back to full login."""
    api = _make_eu_api(brand=1)

    with patch.object(
        api, "login", return_value=_make_token(access_token="Bearer from-login")
    ) as mock_login:
        token = _make_token(refresh_token=None)
        api.refresh_access_token(token)

    mock_login.assert_called_once_with("user@test.com", "MyPassword123!", "1234")


def test_refresh_access_token_falls_back_on_exchange_failure():
    """When _get_access_token raises, fall back to full login."""
    api = _make_eu_api(brand=1)

    with (
        patch.object(api, "_get_stamp", return_value="stamp"),
        patch.object(
            api,
            "_get_access_token",
            side_effect=Exception("Network error"),
        ),
        patch.object(
            api, "login", return_value=_make_token(access_token="Bearer from-login")
        ) as mock_login,
    ):
        token = _make_token()
        result = api.refresh_access_token(token)

    mock_login.assert_called_once_with("user@test.com", "MyPassword123!", "1234")
    assert result.access_token == "Bearer from-login"


def test_refresh_access_token_does_not_call_get_device_id():
    """refresh_access_token should NOT call _get_device_id (no full re-login)."""
    api = _make_eu_api(brand=1)

    with (
        patch.object(api, "_get_stamp", return_value="stamp"),
        patch.object(
            api,
            "_get_access_token",
            return_value=("Bearer", "Bearer new-access", "new-rt", 3600),
        ),
        patch.object(api, "_get_device_id") as mock_device_id,
    ):
        token = _make_token()
        api.refresh_access_token(token)

    mock_device_id.assert_not_called()


def test_refresh_access_token_does_not_call_get_cookies():
    """refresh_access_token should NOT call _get_cookies (no full re-login)."""
    api = _make_eu_api(brand=1)

    with (
        patch.object(api, "_get_stamp", return_value="stamp"),
        patch.object(
            api,
            "_get_access_token",
            return_value=("Bearer", "Bearer new-access", "new-rt", 3600),
        ),
        patch.object(api, "_get_cookies") as mock_cookies,
    ):
        token = _make_token()
        api.refresh_access_token(token)

    mock_cookies.assert_not_called()
