"""Tests for KiaCciApiEU — brand constants, cipher selection, the login
stub flow, the shared get_vehicles path, and the NotImplementedError
cached-state parser stub (zero network)."""

import datetime as dt
from contextlib import ExitStack
from unittest.mock import MagicMock, patch

import pytest

from hyundai_kia_connect_api.exceptions import APIError
from hyundai_kia_connect_api.gspa.cipher_keys import kia_cipher
from hyundai_kia_connect_api.KiaCciApiEU import KiaCciApiEU

# ── Helpers ─────────────────────────────────────────────────────


def _make_kia_api() -> KiaCciApiEU:
    """Create a KiaCciApiEU instance for testing (brand=1 = Kia)."""
    return KiaCciApiEU(region=9, brand=1, language="en")


def _mock_crypto():
    """Return patches for RSA.construct and PKCS1_v1_5.new."""
    mock_cipher = MagicMock()
    mock_cipher.encrypt.return_value = b"\x00" * 256  # fake encrypted password
    return [
        patch("hyundai_kia_connect_api.GspaApiEU.RSA.construct"),
        patch(
            "hyundai_kia_connect_api.GspaApiEU.PKCS1_v1_5.new",
            return_value=mock_cipher,
        ),
    ]


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


# ── Brand constants and cipher selection ─────────────────────


def test_kia_constants_set_correctly():
    """Kia brand sets the correct CCI constants."""
    api = _make_kia_api()
    assert api.ONEAPP_CLIENT_ID == "01b36c86-79e8-486c-8009-15f2ad88d670"
    assert api.ONEAPP_REDIRECT_URI == "https://oneapp.kia.com/redirect"
    assert api.CCI_API_URL == "https://cci-api-eu.kia.com"
    assert api.CCI_DOMAIN_API_URL == "https://cci-api-eu.kia.com/domain/api/"
    assert api.CCI_PACKAGE_ID == "com.kia.oneapp.eu"
    assert api.GSPA_BASE_URL == "https://gspa-ccs-eu.kia.com/"
    assert api.LOGIN_FORM_HOST == "https://idpconnect-eu.kia.com"
    assert api.REQUEST_ID_HEADER == "X-Request-Id"
    assert api.DEVICE_ID_HEADER == "X-Userdevice-Id"
    assert api._cci_client_name == "kia"
    assert api.CCSP_API_URL == "https://gspa-ccs-eu.kia.com"


def test_kia_cipher_is_kia_instance():
    """Kia brand selects the kia cipher instance, not hyundai."""
    api = _make_kia_api()
    assert api.CIPHER_BRAND == "kia"
    assert api._cipher is kia_cipher()


# ── _login_with_password() happy path (stub — zero network) ──────────


def test_kia_login_with_password_success():
    """Kia CCI login flow returns access_token and refresh_token."""
    api = _make_kia_api()
    cci_token_resp = MagicMock(status_code=200)
    cci_token_resp.json.return_value = {
        "accessToken": "cci-access",
        "refreshToken": "KCIREFRESHTOKEN1234567890123456789012345678901234567890",
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
                "hyundai_kia_connect_api.GspaApiEU.ApiImplSession",
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
                "hyundai_kia_connect_api.GspaApiEU.requests.post",
                side_effect=[cci_token_resp, exchange_resp],
            )
        )
        info = api._login_with_password("user@kia.com", "password", "device-1")

    assert info["access_token"] == "Bearer ccs-token"
    assert (
        info["refresh_token"]
        == "KCIREFRESHTOKEN1234567890123456789012345678901234567890"
    )
    assert info["cci_access_token"] == "cci-access"
    assert info["id_token"] == "id-tok"


# ── Vehicle list (shared with Hyundai — same CCI envelope) ────────


def test_kia_get_vehicles_shared_path():
    """Kia get_vehicles hits the Kia CCI domain and parses the shared
    ccspCarId/ccspVehicle envelope (inherited from GspaApiEU)."""
    api = _make_kia_api()
    token = MagicMock(device_id="dev-1", cci_access_token="cci-tok")
    token.non_ccs_token = "nonccs"
    token.exchangeable_token = "exch"
    resp = MagicMock(status_code=200)
    resp.json.return_value = {
        "contents": [
            {
                "ccspCarId": "car-123",
                "vin": "KNAB25120R0123456",
                "vehicleNameView": "Kia EV3",
                "vehicleModelName": "EV3",
                "isEv": True,
            }
        ]
    }
    with patch(
        "hyundai_kia_connect_api.GspaApiEU.requests.get", return_value=resp
    ) as g:
        vehicles = api.get_vehicles(token)

    assert g.call_args[0][0] == (
        "https://cci-api-eu.kia.com/domain/api/v1/vehicle/available-vehicles?detail=true"
    )
    assert len(vehicles) == 1
    assert vehicles[0].id == "car-123"
    assert vehicles[0].VIN == "KNAB25120R0123456"
    assert vehicles[0].name == "Kia EV3"
    assert vehicles[0].model == "EV3"


def test_kia_update_cached_state_requires_ccs_token():
    """update_vehicle_with_cached_state is implemented (D5) and rejects a
    token without CCS credentials before any network call."""
    api = _make_kia_api()
    token = MagicMock()
    token.access_token = None
    token.exchangeable_token = None
    with pytest.raises(APIError):
        api.update_vehicle_with_cached_state(token, MagicMock())


def test_kia_prewakeup_sends_action_body():
    """prewakeup (inherited from GspaApiEU) posts the app-confirmed shape
    {"action": "prewakeup"} to the brand-global prewakeup path — accepted
    with HTTP 202 on a Kia EV6 (2026-09-23). Best-effort: any failure is
    swallowed to None."""
    api = _make_kia_api()
    token = MagicMock()
    token.access_token = "Bearer ccs-token"
    vehicle = MagicMock()
    vehicle.id = "test123"
    vehicle.ccu_ccs2_protocol_support = 2
    with (
        patch.object(KiaCciApiEU, "_get_authenticated_headers") as headers,
        patch("hyundai_kia_connect_api.GspaApiEU.requests.post") as post,
    ):
        headers.return_value = {"Authorization": "Bearer ccs-token"}
        post.return_value = MagicMock(
            status_code=200, json=lambda: {"rc": "0000", "rs": {}}
        )
        result = api.prewakeup(token, vehicle)
    assert post.call_args.args[0].endswith("/gspa/v1/remote/vehicles/test123/prewakeup")
    assert post.call_args.kwargs["json"] == {"action": "prewakeup"}
    assert result == {}


def test_kia_force_refresh_not_implemented():
    """force_refresh_vehicle_state raises NotImplementedError (inherited
    from ApiImpl after the remote-action move to HyundaiCciApiEU)."""
    api = _make_kia_api()
    with pytest.raises(NotImplementedError):
        api.force_refresh_vehicle_state(MagicMock(), MagicMock())


# ── VehicleManager wiring ────────────────────────────────


def test_vehicle_manager_routes_kia_to_kia_cci():
    """VehicleManager.get_implementation_by_region_brand routes Kia EU CCI."""
    from hyundai_kia_connect_api.VehicleManager import VehicleManager

    # REGION_EUROPE_CCI = 9, BRAND_KIA = 1
    api = VehicleManager.get_implementation_by_region_brand(9, 1, "en")
    assert isinstance(api, KiaCciApiEU)


def test_vehicle_manager_routes_genesis_to_error():
    """Genesis EU CCI raises APIError."""
    from hyundai_kia_connect_api.VehicleManager import VehicleManager

    # REGION_EUROPE_CCI = 9, BRAND_GENESIS = 3
    with pytest.raises(APIError, match="Genesis"):
        VehicleManager.get_implementation_by_region_brand(9, 3, "en")


def test_kia_schedule_reservation_charge_na_gated():
    """schedule_reservation_charge_na is implemented (inherited from
    GspaApiEU) but stays gated on Kia until live verification (D6)."""
    api = _make_kia_api()
    with pytest.raises(NotImplementedError):
        api.schedule_reservation_charge_na(
            MagicMock(), MagicMock(), [1], dt.time(9, 0), dt.time(12, 0)
        )
