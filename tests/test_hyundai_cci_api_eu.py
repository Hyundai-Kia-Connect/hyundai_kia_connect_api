"""Tests for HyundaiCciApiEU._login_with_password(), login() flow, refresh_access_token(),
_get_stamp(), and _fetch_ccs_user_id()."""

import datetime as dt
from contextlib import ExitStack
from unittest.mock import MagicMock, patch

import pytest

from hyundai_kia_connect_api.exceptions import (
    AuthenticationError,
)
from hyundai_kia_connect_api.HyundaiCciApiEU import HyundaiCciApiEU
from hyundai_kia_connect_api.Token import Token

# ── Helpers ─────────────────────────────────────────────────────


def _mock_crypto():
    """Return patches for RSA.construct and PKCS1_v1_5.new."""
    mock_cipher = MagicMock()
    mock_cipher.encrypt.return_value = b"\x00" * 256  # fake encrypted password
    return [
        patch("hyundai_kia_connect_api.HyundaiCciApiEU.RSA.construct"),
        patch(
            "hyundai_kia_connect_api.HyundaiCciApiEU.PKCS1_v1_5.new",
            return_value=mock_cipher,
        ),
    ]


def _make_hyundai_api(brand: int = 2) -> HyundaiCciApiEU:
    """Create a HyundaiCciApiEU instance for testing (brand=2 = Hyundai)."""
    return HyundaiCciApiEU(region=1, brand=brand, language="en")


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
    WAF-detection check in _login_with_password does not trigger.
    """
    authorize_resp = MagicMock(
        text="", url="https://idpconnect-eu.hyundai.com/authorize"
    )
    session = MagicMock()
    session.get.side_effect = [authorize_resp, certs_resp]
    session.post.return_value = signin_resp
    return session


def _signin_resp(location: str) -> MagicMock:
    """A 302 signin response with the given Location header."""
    resp = MagicMock(status_code=302)
    resp.headers = {"location": location}
    return resp


# ── _login_with_password() error paths ─────────────────────


def test_login_with_password_certs_endpoint_fails():
    """Certs endpoint returns non-200 -> AuthenticationError."""
    api = _make_hyundai_api()
    with ExitStack() as stack:
        stack.enter_context(
            patch(
                "hyundai_kia_connect_api.HyundaiCciApiEU.ApiImplSession",
                return_value=_cci_session(MagicMock(status_code=500), MagicMock()),
            )
        )
        for p in _mock_crypto():
            stack.enter_context(p)
        with pytest.raises(AuthenticationError, match="failed to fetch RSA certs"):
            api._login_with_password("user@test.com", "password", "device-1")


def test_login_with_password_signin_returns_non_302():
    """Signin returns 200 instead of 302 -> AuthenticationError."""
    api = _make_hyundai_api()
    signin_resp = MagicMock(status_code=200, text="Login page")
    with ExitStack() as stack:
        stack.enter_context(
            patch(
                "hyundai_kia_connect_api.HyundaiCciApiEU.ApiImplSession",
                return_value=_cci_session(_certs_response(), signin_resp),
            )
        )
        for p in _mock_crypto():
            stack.enter_context(p)
        with pytest.raises(AuthenticationError, match="Signin failed"):
            api._login_with_password("user@test.com", "password", "device-1")


def test_login_with_password_signin_no_code_in_redirect():
    """Signin redirect has no code parameter -> AuthenticationError."""
    api = _make_hyundai_api()
    with ExitStack() as stack:
        stack.enter_context(
            patch(
                "hyundai_kia_connect_api.HyundaiCciApiEU.ApiImplSession",
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
            api._login_with_password("user@test.com", "password", "device-1")


def test_login_with_password_signin_error_in_redirect():
    """Signin redirect contains error parameter -> AuthenticationError."""
    api = _make_hyundai_api()
    with ExitStack() as stack:
        stack.enter_context(
            patch(
                "hyundai_kia_connect_api.HyundaiCciApiEU.ApiImplSession",
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
            api._login_with_password("user@test.com", "wrong-password", "device-1")


def test_login_with_password_signin_redirect_to_login_page():
    """Signin redirects back to authorize page -> AuthenticationError."""
    api = _make_hyundai_api()
    with ExitStack() as stack:
        stack.enter_context(
            patch(
                "hyundai_kia_connect_api.HyundaiCciApiEU.ApiImplSession",
                return_value=_cci_session(
                    _certs_response(),
                    _signin_resp(
                        "https://idpconnect-eu.hyundai.com/auth/api/v2/user/oauth2/authorize?state=ccsp"
                    ),
                ),
            )
        )
        for p in _mock_crypto():
            stack.enter_context(p)
        with pytest.raises(AuthenticationError, match="returned to login page"):
            api._login_with_password("user@test.com", "password", "device-1")


def test_login_with_password_waf_block_detected():
    """Authorize returns the WAF block page -> AuthenticationError with #1273 ref."""
    api = _make_hyundai_api()
    authorize_resp = MagicMock(
        text="It was classified as an abusing request and blocked",
        url="https://idpconnect-eu.hyundai.com/error?status=400",
    )
    session = MagicMock()
    session.get.return_value = authorize_resp
    with ExitStack() as stack:
        stack.enter_context(
            patch(
                "hyundai_kia_connect_api.HyundaiCciApiEU.ApiImplSession",
                return_value=session,
            )
        )
        for p in _mock_crypto():
            stack.enter_context(p)
        with pytest.raises(AuthenticationError, match="abusing request"):
            api._login_with_password("user@test.com", "password", "device-1")


def test_login_with_password_cci_token_exchange_fails():
    """CCI token endpoint returns non-200 -> AuthenticationError."""
    api = _make_hyundai_api()
    token_resp = MagicMock(status_code=400, text="Bad request")
    with ExitStack() as stack:
        stack.enter_context(
            patch(
                "hyundai_kia_connect_api.HyundaiCciApiEU.ApiImplSession",
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
                "hyundai_kia_connect_api.HyundaiCciApiEU.requests.post",
                return_value=token_resp,
            )
        )
        with pytest.raises(AuthenticationError, match="CCI token exchange failed"):
            api._login_with_password("user@test.com", "password", "device-1")


def test_login_with_password_success():
    """Full CCI login flow returns access_token, refresh_token and CCI fields."""
    api = _make_hyundai_api()  # Hyundai (brand=2)
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
                "hyundai_kia_connect_api.HyundaiCciApiEU.ApiImplSession",
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
                "hyundai_kia_connect_api.HyundaiCciApiEU.requests.post",
                side_effect=[cci_token_resp, exchange_resp],
            )
        )
        info = api._login_with_password("user@test.com", "password", "device-1")

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


# ── Hyundai-only brand constraint ────────────────────────


def test_kia_brand_raises_not_implemented():
    """Kia brand should raise NotImplementedError — use KiaUvoApiEU for Kia EU."""
    with pytest.raises(NotImplementedError, match="Kia CCI EU not yet implemented"):
        HyundaiCciApiEU(region=1, brand=1, language="en")


def test_genesis_brand_raises_not_implemented():
    """Genesis brand should raise NotImplementedError."""
    with pytest.raises(NotImplementedError, match="Genesis CCI EU not yet implemented"):
        HyundaiCciApiEU(region=1, brand=3, language="en")


def test_hyundai_constants_set_correctly():
    """Hyundai brand sets the correct CCI constants."""
    api = _make_hyundai_api()
    assert api.ONEAPP_CLIENT_ID == "4f4953b5-02e1-4dbc-8599-87e983ee1be5"
    assert api.ONEAPP_REDIRECT_URI == "https://oneapp.hyundai.com/redirect"
    assert api.CCI_API_URL == "https://cci-api-eu.hyundai.com"
    assert api.CCI_DOMAIN_API_URL == "https://cci-api-eu.hyundai.com/domain/api/"
    assert api._cci_package_id == "com.hyundai.oneapp.eu"
    assert api._cci_client_name == "hyundai"


# ── login() flow routing ────────────────────────


def test_login_calls_register_device_and_fetch_ccs_user_id():
    """login() calls _register_device and _fetch_ccs_user_id after password login."""
    api = _make_hyundai_api()
    login_info = {
        "access_token": "Bearer ccs-token",
        "refresh_token": "CCIREFRESHHTOKEN1234567890123456789012345678901234567890",
        "expires_in": 3600,
        "valid_until": dt.datetime.now(dt.UTC) + dt.timedelta(hours=1),
        "cci_access_token": "cci-at",
        "exchangeable_token": "exch-at",
        "exchangeable_refresh_token": "exch-rt",
        "non_ccs_token": "nonccs",
        "non_ccs_refresh_token": "nonccs-rt",
        "id_token": "id-tok",
    }
    with (
        patch.object(api, "_login_with_password", return_value=login_info),
        patch.object(api, "_register_device") as mock_reg,
        patch.object(api, "_fetch_ccs_user_id") as mock_fetch_uid,
    ):
        token = api.login("user@test.com", "MyPassword123!", pin="1234")

    assert token.access_token == "Bearer ccs-token"
    assert token.pin == "1234"
    assert token.username == "user@test.com"
    assert token.cci_access_token == "cci-at"
    # device_id should be a UUID string
    assert token.device_id is not None
    assert len(token.device_id) == 36  # UUID format
    mock_reg.assert_called_once()
    mock_fetch_uid.assert_called_once()


# ── refresh_access_token() tests ───────────────────────────────


def _make_token(**overrides) -> Token:
    """Create a Token instance with sensible CCI defaults for testing."""
    defaults = {
        "username": "user@test.com",
        "password": "MyPassword123!",
        "access_token": "Bearer old-ccs-token",
        "refresh_token": "OLDCCIREFRESHTOKEN1234567890123456789012345678901234567",
        "device_id": "12345678-1234-1234-1234-123456789abc",
        "valid_until": dt.datetime.now(dt.UTC) + dt.timedelta(hours=1),
        "pin": "1234",
        "cci_access_token": "old-cci-at",
        "exchangeable_token": "old-exch-at",
        "exchangeable_refresh_token": "old-exch-rt",
        "non_ccs_token": "old-nonccs",
        "non_ccs_refresh_token": "old-nonccs-rt",
        "id_token": "old-id-tok",
        "ccs_user_id": "test-uid-123",
    }
    defaults.update(overrides)
    return Token(**defaults)


def test_refresh_access_token_uses_cci_refresh():
    """refresh_access_token calls _refresh_cci_token when CCI tokens are present."""
    api = _make_hyundai_api()
    new_token = _make_token(access_token="Bearer new-ccs-token")

    with patch.object(
        api, "_refresh_cci_token", return_value=new_token
    ) as mock_refresh:
        token = _make_token()
        result = api.refresh_access_token(token)

    mock_refresh.assert_called_once_with(token)
    assert result.access_token == "Bearer new-ccs-token"


def test_refresh_access_token_falls_back_on_failure():
    """When _refresh_cci_token raises, fall back to full login."""
    api = _make_hyundai_api()

    with (
        patch.object(api, "_refresh_cci_token", side_effect=Exception("Network error")),
        patch.object(
            api, "login", return_value=_make_token(access_token="Bearer from-login")
        ) as mock_login,
    ):
        token = _make_token()
        result = api.refresh_access_token(token)

    mock_login.assert_called_once_with("user@test.com", "MyPassword123!", "1234")
    assert result.access_token == "Bearer from-login"


def test_refresh_access_token_falls_back_on_missing_cci_tokens():
    """When token has no cci_access_token/non_ccs_token, fall back to full login."""
    api = _make_hyundai_api()

    with patch.object(
        api, "login", return_value=_make_token(access_token="Bearer from-login")
    ) as mock_login:
        token = _make_token(cci_access_token=None, non_ccs_token=None)
        result = api.refresh_access_token(token)

    mock_login.assert_called_once_with("user@test.com", "MyPassword123!", "1234")
    assert result.access_token == "Bearer from-login"


def test_refresh_cci_token_uses_v1_endpoint():
    """_refresh_cci_token posts to v1/auth/token-refresh (not v2)."""
    api = _make_hyundai_api()
    refresh_resp = MagicMock(status_code=200)
    refresh_resp.json.return_value = {
        "accessToken": "new-cci-at",
        "refreshToken": "new-cci-rt",
        "exchangeableAccessToken": "new-exch-at",
        "exchangeableRefreshToken": "new-exch-rt",
        "nonCcsToken": "new-nonccs",
        "nonCcsRefreshToken": "new-nonccs-rt",
        "idToken": "new-id-tok",
    }
    exchange_resp = MagicMock(status_code=200)
    exchange_resp.json.return_value = {
        "accessToken": "new-ccs-token",
        "expiresTime": 3600,
    }
    with (
        patch(
            "hyundai_kia_connect_api.HyundaiCciApiEU.requests.post",
            side_effect=[refresh_resp, exchange_resp],
        ) as mock_post,
    ):
        token = _make_token()
        result = api.refresh_access_token(token)

    # First call should be to v1/auth/token-refresh
    first_call_url = mock_post.call_args_list[0].args[0]
    assert "v1/auth/token-refresh" in first_call_url
    assert "v2/auth/token-refresh" not in first_call_url
    assert result.access_token == "Bearer new-ccs-token"


# ── _get_stamp() GSPA X-Stamp ────────────────────────


def test_get_stamp_returns_valid_stamp():
    """_get_stamp returns a non-None base64 stamp for EU region."""
    api = _make_hyundai_api()
    token = _make_token()
    stamp = api._get_stamp(token)
    assert stamp is not None
    assert isinstance(stamp, str)
    # Should be valid base64
    import base64

    base64.b64decode(stamp + "==")  # should not raise


def test_get_stamp_uses_ccs_user_id():
    """_get_stamp uses token.ccs_user_id as the user_id in the X-Stamp payload."""
    api = _make_hyundai_api()
    token = _make_token(ccs_user_id="my-test-uid")
    # compute_x_stamp is imported inside _get_stamp from .gspa, so we patch
    # it at the source module rather than at HyundaiCciApiEU level.
    stamp = api._get_stamp(token)
    assert stamp is not None


# ── _fetch_ccs_user_id() JWT extraction ────────────────────


def test_fetch_ccs_user_id_from_ccs_token_jwt():
    """_fetch_ccs_user_id extracts uid from CCS token JWT."""
    api = _make_hyundai_api()
    # Create a fake JWT with uid claim in payload
    import base64

    header = base64.b64encode(b'{"alg":"RS256"}').decode().rstrip("=")
    payload = (
        base64.b64encode(b'{"uid":"test-uid-456","sub":"user-123"}')
        .decode()
        .rstrip("=")
    )
    fake_jwt = f"{header}.{payload}.signature"

    token = _make_token(access_token="Bearer " + fake_jwt, ccs_user_id=None)
    api._fetch_ccs_user_id(token)

    assert token.ccs_user_id == "test-uid-456"


def test_fetch_ccs_user_id_fallback_to_id_token_sub():
    """When CCS token has no uid, fall back to id_token's sub claim."""
    api = _make_hyundai_api()
    import base64

    # CCS token JWT without uid
    ccs_header = base64.b64encode(b'{"alg":"RS256"}').decode().rstrip("=")
    ccs_payload = base64.b64encode(b'{"sub":"ccs-sub"}').decode().rstrip("=")
    ccs_jwt = f"{ccs_header}.{ccs_payload}.sig"

    # id_token JWT with sub
    id_header = base64.b64encode(b'{"alg":"RS256"}').decode().rstrip("=")
    id_payload = base64.b64encode(b'{"sub":"id-sub-789"}').decode().rstrip("=")
    id_jwt = f"{id_header}.{id_payload}.sig"

    token = _make_token(
        access_token="Bearer " + ccs_jwt,
        id_token=id_jwt,
        ccs_user_id=None,
    )
    api._fetch_ccs_user_id(token)

    assert token.ccs_user_id == "id-sub-789"


def test_fetch_ccs_user_id_preserves_existing():
    """When ccs_user_id is already set, _fetch_ccs_user_id does not overwrite."""
    api = _make_hyundai_api()
    token = _make_token(ccs_user_id="existing-uid")
    api._fetch_ccs_user_id(token)
    assert token.ccs_user_id == "existing-uid"


# ── test_token() ────────────────────────


def test_test_token_returns_true_on_200():
    """test_token returns True when CCI API returns 200."""
    api = _make_hyundai_api()
    token = _make_token()
    with patch(
        "hyundai_kia_connect_api.HyundaiCciApiEU.requests.get",
        return_value=MagicMock(status_code=200),
    ):
        assert api.test_token(token) is True


def test_test_token_returns_false_on_non_200():
    """test_token returns False when CCI API returns non-200."""
    api = _make_hyundai_api()
    token = _make_token()
    with patch(
        "hyundai_kia_connect_api.HyundaiCciApiEU.requests.get",
        return_value=MagicMock(status_code=401),
    ):
        assert api.test_token(token) is False
