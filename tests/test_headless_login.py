"""Tests for KiaUvoApiEU._login_with_password(), login() flow routing, and refresh_access_token()."""

import datetime as dt
from contextlib import ExitStack
from unittest.mock import MagicMock, patch

import pytest

from hyundai_kia_connect_api.exceptions import AuthenticationError, ConsentRequiredError
from hyundai_kia_connect_api.KiaUvoApiEU import KiaUvoApiEU
from hyundai_kia_connect_api.Token import Token

# ── Helpers ─────────────────────────────────────────────────────


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


def _certs_response() -> MagicMock:
    """A 200 certs response with a fake JWK."""
    resp = MagicMock(status_code=200)
    resp.json.return_value = {
        "retValue": {
            "kid": "test-kid",
            "n": "AJRQISPa0AJRQISPa0AJRQISPa0AJRQISPa0AJRQISPa0A",
            "e": "AQAB",
        }
    }
    return resp


def _cci_session(certs_resp: MagicMock, signin_resp: MagicMock) -> MagicMock:
    """A mock ApiImplSession for CCI login steps 1-3 (authorize, certs, signin).

    The authorize response is a non-WAF page (empty text, clean url) so the
    WAF-detection check in _login_with_password_cci does not trigger.
    """
    authorize_resp = MagicMock(text="", url="https://idpconnect-eu.kia.com/authorize")
    session = MagicMock()
    session.get.side_effect = [authorize_resp, certs_resp]
    session.post.return_value = signin_resp
    return session


def _signin_resp(location: str) -> MagicMock:
    """A 302 signin response with the given Location header."""
    resp = MagicMock(status_code=302)
    resp.headers = {"location": location}
    return resp


# ── _login_with_password_cci() error paths ─────────────────────


def test_login_with_password_certs_endpoint_fails():
    """Certs endpoint returns non-200 -> AuthenticationError."""
    api = _make_eu_api(brand=1)
    with ExitStack() as stack:
        stack.enter_context(
            patch(
                "hyundai_kia_connect_api.KiaUvoApiEU.ApiImplSession",
                return_value=_cci_session(MagicMock(status_code=500), MagicMock()),
            )
        )
        for p in _mock_crypto():
            stack.enter_context(p)
        with pytest.raises(AuthenticationError, match="failed to fetch RSA certs"):
            api._login_with_password_cci("user@test.com", "password", "device-1")


def test_login_with_password_signin_returns_non_302():
    """Signin returns 200 instead of 302 -> AuthenticationError."""
    api = _make_eu_api(brand=1)
    signin_resp = MagicMock(status_code=200, text="Login page")
    with ExitStack() as stack:
        stack.enter_context(
            patch(
                "hyundai_kia_connect_api.KiaUvoApiEU.ApiImplSession",
                return_value=_cci_session(_certs_response(), signin_resp),
            )
        )
        for p in _mock_crypto():
            stack.enter_context(p)
        with pytest.raises(AuthenticationError, match="Signin failed"):
            api._login_with_password_cci("user@test.com", "password", "device-1")


def test_login_with_password_signin_no_code_in_redirect():
    """Signin redirect has no code parameter -> AuthenticationError."""
    api = _make_eu_api(brand=1)
    with ExitStack() as stack:
        stack.enter_context(
            patch(
                "hyundai_kia_connect_api.KiaUvoApiEU.ApiImplSession",
                return_value=_cci_session(
                    _certs_response(),
                    _signin_resp("https://example.com/login?no_code=true"),
                ),
            )
        )
        for p in _mock_crypto():
            stack.enter_context(p)
        with pytest.raises(
            AuthenticationError, match="unexpected redirect after signin"
        ):
            api._login_with_password_cci("user@test.com", "password", "device-1")


def test_login_with_password_signin_error_in_redirect():
    """Signin redirect contains error parameter -> AuthenticationError."""
    api = _make_eu_api(brand=1)
    with ExitStack() as stack:
        stack.enter_context(
            patch(
                "hyundai_kia_connect_api.KiaUvoApiEU.ApiImplSession",
                return_value=_cci_session(
                    _certs_response(),
                    _signin_resp(
                        "https://example.com/callback?error=access_denied"
                        "&error_description=Invalid+credentials"
                    ),
                ),
            )
        )
        for p in _mock_crypto():
            stack.enter_context(p)
        with pytest.raises(AuthenticationError, match="Authentication rejected"):
            api._login_with_password_cci("user@test.com", "wrong-password", "device-1")


def test_login_with_password_signin_redirect_to_login_page():
    """Signin redirects back to authorize page -> AuthenticationError."""
    api = _make_eu_api(brand=1)
    with ExitStack() as stack:
        stack.enter_context(
            patch(
                "hyundai_kia_connect_api.KiaUvoApiEU.ApiImplSession",
                return_value=_cci_session(
                    _certs_response(),
                    _signin_resp(
                        "https://idpconnect-eu.kia.com/auth/api/v2/user/oauth2/authorize?state=ccsp"
                    ),
                ),
            )
        )
        for p in _mock_crypto():
            stack.enter_context(p)
        with pytest.raises(AuthenticationError, match="returned to login page"):
            api._login_with_password_cci("user@test.com", "password", "device-1")


def test_login_with_password_signin_consent_spa_redirect():
    """Signin redirects to /web/v1/user/authorization SPA (consent page)."""
    api = _make_eu_api(brand=1)
    with ExitStack() as stack:
        stack.enter_context(
            patch(
                "hyundai_kia_connect_api.KiaUvoApiEU.ApiImplSession",
                return_value=_cci_session(
                    _certs_response(),
                    _signin_resp(
                        "https://prd.eu-ccapi.kia.com/web/v1/user/authorization"
                    ),
                ),
            )
        )
        for p in _mock_crypto():
            stack.enter_context(p)
        with pytest.raises(ConsentRequiredError, match="consent is required"):
            api._login_with_password_cci("user@test.com", "password", "device-1")


def test_login_with_password_waf_block_detected():
    """Authorize returns the WAF block page -> AuthenticationError with #1273 ref."""
    api = _make_eu_api(brand=1)
    authorize_resp = MagicMock(
        text="It was classified as an abusing request and blocked",
        url="https://idpconnect-eu.kia.com/error?status=400",
    )
    session = MagicMock()
    session.get.return_value = authorize_resp
    with ExitStack() as stack:
        stack.enter_context(
            patch(
                "hyundai_kia_connect_api.KiaUvoApiEU.ApiImplSession",
                return_value=session,
            )
        )
        for p in _mock_crypto():
            stack.enter_context(p)
        with pytest.raises(AuthenticationError, match="abusing request"):
            api._login_with_password_cci("user@test.com", "password", "device-1")


def test_login_with_password_cci_token_exchange_fails():
    """CCI token endpoint returns non-200 -> AuthenticationError."""
    api = _make_eu_api(brand=1)
    token_resp = MagicMock(status_code=400, text="Bad request")
    with ExitStack() as stack:
        stack.enter_context(
            patch(
                "hyundai_kia_connect_api.KiaUvoApiEU.ApiImplSession",
                return_value=_cci_session(
                    _certs_response(),
                    _signin_resp("https://example.com/cb?code=abc123"),
                ),
            )
        )
        for p in _mock_crypto():
            stack.enter_context(p)
        stack.enter_context(
            patch(
                "hyundai_kia_connect_api.KiaUvoApiEU.requests.post",
                return_value=token_resp,
            )
        )
        with pytest.raises(AuthenticationError, match="CCI token exchange failed"):
            api._login_with_password_cci("user@test.com", "password", "device-1")


def test_login_with_password_success():
    """Full CCI login flow returns access_token, refresh_token and CCI fields."""
    api = _make_eu_api(brand=2)  # Hyundai
    cci_token_resp = MagicMock(status_code=200)
    cci_token_resp.json.return_value = {
        "accessToken": "cci-access",
        "refreshToken": "CCIREFRESHTOKEN1234567890123456789012345678901234567890",
        "exchangeableAccessToken": "exch-at",
        "exchangeableRefreshToken": "exch-rt",
        "nonCcsToken": "nonccs",
        "nonCcsRefreshToken": "nonccs-rt",
        "idToken": "id-tok",
        "expiresIn": 3599,
    }
    exchange_resp = MagicMock(status_code=200)
    exchange_resp.json.return_value = {
        "accessToken": "ccs-token",
        "expiresTime": 86400,
    }
    with ExitStack() as stack:
        stack.enter_context(
            patch(
                "hyundai_kia_connect_api.KiaUvoApiEU.ApiImplSession",
                return_value=_cci_session(
                    _certs_response(),
                    _signin_resp("https://example.com/cb?code=abc123"),
                ),
            )
        )
        for p in _mock_crypto():
            stack.enter_context(p)
        stack.enter_context(
            patch(
                "hyundai_kia_connect_api.KiaUvoApiEU.requests.post",
                side_effect=[cci_token_resp, exchange_resp],
            )
        )
        info = api._login_with_password_cci("user@test.com", "password", "device-1")

    assert info["access_token"] == "Bearer ccs-token"
    assert (
        info["refresh_token"]
        == "CCIREFRESHTOKEN1234567890123456789012345678901234567890"
    )
    assert info["cci_access_token"] == "cci-access"
    assert info["exchangeable_token"] == "exch-at"
    assert info["non_ccs_token"] == "nonccs"
    assert info["id_token"] == "id-tok"
    # expiresTime is a TTL in seconds (86400 = 24h), so valid_until is ~24h
    # from now — not an epoch in 1970.
    now = dt.datetime.now(dt.UTC)
    assert info["valid_until"] > now
    assert (info["valid_until"] - now).total_seconds() == pytest.approx(86400, abs=5)


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
    """Plaintext password invokes _login_with_password(user, pw, device_id)."""
    api = _make_eu_api(brand=1)  # Kia

    with (
        patch.object(api, "_get_stamp", return_value="stamp"),
        patch.object(api, "_get_device_id", return_value="device-123"),
        patch.object(api, "_get_cookies", return_value={}),
        patch.object(api, "_set_session_language"),
        patch.object(
            api,
            "_login_with_password",
            return_value={
                "access_token": "Bearer headless-access-token",
                "refresh_token": "HEADLESSREFRESHTOKEN123456789012345678",
                "expires_in": 3600,
            },
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
            return_value={
                "access_token": "Bearer genesis-access-token",
                "refresh_token": "GENESISREFRESHTOKEN12345678901234567",
                "expires_in": 3600,
            },
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
        pytest.raises(AuthenticationError, match="Signin failed"),
    ):
        api.login("user@test.com", "MyPassword123!")


def test_genesis_uses_cci_login_constants():
    """Genesis EU has the OneApp/CCI constants (WAF bypass)."""
    api = _make_eu_api(brand=3)  # Genesis
    assert api.ONEAPP_CLIENT_ID == "50e3b8b0-ced5-43b7-8a42-f86ac92fe50e"
    assert api.ONEAPP_REDIRECT_URI == "https://oneapp.genesis.com/redirect"
    assert api.CCI_API_URL == "https://cci-api-eu.genesis.com"
    assert api.CCI_DOMAIN_API_URL == "https://cci-api-eu.genesis.com/domain/api/"
    assert api._cci_package_id == "com.genesis.oneapp.eu"
    assert api._cci_client_name == "genesis"


def test_genesis_login_routes_to_login_with_password_with_device_id():
    """Genesis _login_with_password delegates to the CCI flow with device_id."""
    api = _make_eu_api(brand=3)  # Genesis

    cci_return = {
        "access_token": "Bearer genesis-ccs-token",
        "refresh_token": "GENESISCCIREFRESHTOKEN1234567890123456789012345678901234567890",
        "expires_in": 3600,
        "valid_until": dt.datetime.now(dt.UTC) + dt.timedelta(hours=1),
        "cci_access_token": "genesis-cci-at",
        "exchangeable_token": "genesis-exch",
        "exchangeable_refresh_token": "genesis-exch-rt",
        "non_ccs_token": "genesis-nonccs",
        "non_ccs_refresh_token": "genesis-nonccs-rt",
        "id_token": "genesis-id",
    }
    with patch.object(
        api, "_login_with_password_cci", return_value=cci_return
    ) as mock_cci:
        result = api._login_with_password(
            "user@test.com", "MyPassword123!", "device-123"
        )

    mock_cci.assert_called_once_with("user@test.com", "MyPassword123!", "device-123")
    assert result is cci_return


# ── refresh_access_token() tests ───────────────────────────────


def _make_token(**overrides) -> Token:
    """Create a Token instance with sensible defaults for testing."""
    defaults = {
        "username": "user@test.com",
        "password": "MyPassword123!",
        "access_token": "Bearer old-access-token",
        "refresh_token": "OLDREFRESHTOKEN1234567890123456789012345678",
        "device_id": "device-123",
        "valid_until": dt.datetime.now(dt.UTC) + dt.timedelta(hours=1),
        "pin": "1234",
    }
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
