"""Tests for KiaUvoApiEU._login_with_password() and login() flow routing."""

from contextlib import ExitStack
from unittest.mock import MagicMock, patch

import pytest

from hyundai_kia_connect_api.KiaUvoApiEU import KiaUvoApiEU
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
        with pytest.raises(AuthenticationError, match="Failed to fetch RSA certs"):
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
        with pytest.raises(AuthenticationError, match="No authorization code"):
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
        with pytest.raises(AuthenticationError, match="Signin rejected"):
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
        with pytest.raises(AuthenticationError, match="Token exchange failed"):
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
