"""Tests for headless EU login (headless_login.py) and KiaUvoApiEU.login() flow."""

from contextlib import ExitStack
from unittest.mock import MagicMock, patch

import pytest

from hyundai_kia_connect_api.KiaUvoApiEU import KiaUvoApiEU
from hyundai_kia_connect_api.exceptions import AuthenticationError


# ── BluelinkToken dataclass ──────────────────────────────────


def test_bluelink_token_dataclass():
    from hyundai_kia_connect_api.headless_login import BluelinkToken

    token = BluelinkToken(access_token="at", refresh_token="rt", expires_in=3600)
    assert token.access_token == "at"
    assert token.refresh_token == "rt"
    assert token.expires_in == 3600


# ── Helper: patches for get_token() tests ─────────────────────
# RSA/PKCS1v15 crypto is not under test, so we mock it out.


def _mock_crypto():
    """Return patches for RSA.construct and PKCS1_v1_5.new."""
    mock_cipher = MagicMock()
    mock_cipher.encrypt.return_value = b"\x00" * 256  # fake encrypted password
    return [
        patch("hyundai_kia_connect_api.headless_login.RSA.construct"),
        patch(
            "hyundai_kia_connect_api.headless_login.PKCS1_v1_5.new",
            return_value=mock_cipher,
        ),
    ]


# ── get_token() error paths ──────────────────────────────────


def test_get_token_unsupported_brand():
    from hyundai_kia_connect_api.headless_login import get_token

    with pytest.raises(ValueError, match="not supported for headless login"):
        get_token("user@test.com", "password", 99)


def test_get_token_certs_endpoint_fails():
    """Certs endpoint returns non-200 -> AuthenticationError."""
    from hyundai_kia_connect_api.headless_login import get_token

    mock_response = MagicMock()
    mock_response.status_code = 500

    mock_session = MagicMock()
    mock_session.get.return_value = mock_response

    with patch(
        "hyundai_kia_connect_api.headless_login.curl_requests.Session",
        return_value=mock_session,
    ):
        with pytest.raises(AuthenticationError, match="Failed to fetch RSA certs"):
            get_token("user@test.com", "password", 1)


def test_get_token_signin_returns_non_302():
    """Signin endpoint returns 200 instead of 302 -> AuthenticationError."""
    from hyundai_kia_connect_api.headless_login import get_token

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
                "hyundai_kia_connect_api.headless_login.curl_requests.Session",
                return_value=mock_session,
            )
        )
        for p in _mock_crypto():
            stack.enter_context(p)
        with pytest.raises(AuthenticationError, match="Signin failed"):
            get_token("user@test.com", "password", 1)


def test_get_token_signin_no_code_in_redirect():
    """Signin redirect has no code parameter -> AuthenticationError."""
    from hyundai_kia_connect_api.headless_login import get_token

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
                "hyundai_kia_connect_api.headless_login.curl_requests.Session",
                return_value=mock_session,
            )
        )
        for p in _mock_crypto():
            stack.enter_context(p)
        with pytest.raises(AuthenticationError, match="No authorization code"):
            get_token("user@test.com", "password", 1)


def test_get_token_signin_error_in_redirect():
    """Signin redirect contains error parameter -> AuthenticationError."""
    from hyundai_kia_connect_api.headless_login import get_token

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
                "hyundai_kia_connect_api.headless_login.curl_requests.Session",
                return_value=mock_session,
            )
        )
        for p in _mock_crypto():
            stack.enter_context(p)
        with pytest.raises(AuthenticationError, match="Signin rejected"):
            get_token("user@test.com", "wrong-password", 1)


def test_get_token_signin_redirect_to_login_page():
    """Signin redirects back to authorize page -> AuthenticationError with helpful message."""
    from hyundai_kia_connect_api.headless_login import get_token

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
    # The real API redirects back to the authorize URL when login fails
    # (no "error" param, no "code" param — just redirects back to authorize)
    signin_resp.headers = {
        "location": "https://idpconnect-eu.kia.com/auth/api/v2/user/oauth2/authorize?state=ccsp"
    }

    mock_session = MagicMock()
    mock_session.get.return_value = certs_resp
    mock_session.post.return_value = signin_resp

    with ExitStack() as stack:
        stack.enter_context(
            patch(
                "hyundai_kia_connect_api.headless_login.curl_requests.Session",
                return_value=mock_session,
            )
        )
        for p in _mock_crypto():
            stack.enter_context(p)
        with pytest.raises(AuthenticationError, match="redirected back to login page"):
            get_token("user@test.com", "password", 1)


def test_get_token_signin_consent_spa_redirect():
    """Kia EU redirects to /web/v1/user/authorization SPA (consent page)."""
    from hyundai_kia_connect_api.headless_login import get_token

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
        "location": ("https://prd.eu-ccapi.kia.com:8080/web/v1/user/authorization")
    }

    mock_session = MagicMock()
    mock_session.get.return_value = certs_resp
    mock_session.post.return_value = signin_resp

    with ExitStack() as stack:
        stack.enter_context(
            patch(
                "hyundai_kia_connect_api.headless_login.curl_requests.Session",
                return_value=mock_session,
            )
        )
        for p in _mock_crypto():
            stack.enter_context(p)
        with pytest.raises(AuthenticationError, match="consent page"):
            get_token("user@test.com", "password", 1)


def test_get_token_token_exchange_fails():
    """Token exchange returns non-200 -> AuthenticationError."""
    from hyundai_kia_connect_api.headless_login import get_token

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
                "hyundai_kia_connect_api.headless_login.curl_requests.Session",
                return_value=mock_session,
            )
        )
        stack.enter_context(
            patch(
                "hyundai_kia_connect_api.headless_login.curl_requests.post",
                return_value=token_resp,
            )
        )
        for p in _mock_crypto():
            stack.enter_context(p)
        with pytest.raises(AuthenticationError, match="Token exchange failed"):
            get_token("user@test.com", "password", 1)


def test_get_token_success():
    """Full successful headless login flow returns BluelinkToken."""
    from hyundai_kia_connect_api.headless_login import BluelinkToken, get_token

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
        "access_token": "Bearer test-access-token",
        "refresh_token": "TESTRFTOKEN12345678901234567890123456789012345678",
        "expires_in": 86400,
    }

    mock_session = MagicMock()
    mock_session.get.return_value = certs_resp
    mock_session.post.return_value = signin_resp

    with ExitStack() as stack:
        stack.enter_context(
            patch(
                "hyundai_kia_connect_api.headless_login.curl_requests.Session",
                return_value=mock_session,
            )
        )
        stack.enter_context(
            patch(
                "hyundai_kia_connect_api.headless_login.curl_requests.post",
                return_value=token_resp,
            )
        )
        for p in _mock_crypto():
            stack.enter_context(p)
        result = get_token("user@test.com", "password", 2)

    assert isinstance(result, BluelinkToken)
    assert result.access_token == "Bearer test-access-token"
    assert result.refresh_token == "TESTRFTOKEN12345678901234567890123456789012345678"
    assert result.expires_in == 86400


# ── KiaUvoApiEU.login() flow routing ────────────────────────


def _make_eu_api(brand: int = 1) -> KiaUvoApiEU:
    """Create a KiaUvoApiEU instance for testing."""
    return KiaUvoApiEU(region=1, brand=brand, language="en")


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


def test_login_plaintext_password_calls_headless_login():
    """Plaintext password for Kia/Hyundai invokes get_token() from headless_login."""
    api = _make_eu_api(brand=1)  # Kia

    mock_bluelink_token = MagicMock()
    mock_bluelink_token.access_token = "headless-access-token"
    mock_bluelink_token.refresh_token = "HEADLESSREFRESHTOKEN123456789012345678"
    mock_bluelink_token.expires_in = 3600

    with (
        patch.object(api, "_get_stamp", return_value="stamp"),
        patch.object(api, "_get_device_id", return_value="device-123"),
        patch.object(api, "_get_cookies", return_value={}),
        patch.object(api, "_set_session_language"),
        patch(
            "hyundai_kia_connect_api.headless_login.get_token",
            return_value=mock_bluelink_token,
        ),
    ):
        token = api.login("user@test.com", "MyPassword123!", pin="1234")

    assert token.access_token == "headless-access-token"
    assert token.refresh_token == "HEADLESSREFRESHTOKEN123456789012345678"
    assert token.pin == "1234"


def test_login_plaintext_password_genesis_raises():
    """Plaintext password for Genesis raises AuthenticationError."""
    api = _make_eu_api(brand=3)  # Genesis

    with (
        patch.object(api, "_get_stamp", return_value="stamp"),
        patch.object(api, "_get_device_id", return_value="device-123"),
        patch.object(api, "_get_cookies", return_value={}),
        patch.object(api, "_set_session_language"),
    ):
        with pytest.raises(
            AuthenticationError,
            match="Username/password login is only supported",
        ):
            api.login("user@test.com", "MyPassword123!")
