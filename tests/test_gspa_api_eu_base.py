"""GspaApiEU base — header builder, stamp delegation, and _gspa_get tests."""

import datetime as dt
import json
from unittest.mock import MagicMock, patch

import pytest

from hyundai_kia_connect_api.const import ENGINE_TYPES
from hyundai_kia_connect_api.exceptions import APIError
from hyundai_kia_connect_api.HyundaiCciApiEU import HyundaiCciApiEU
from hyundai_kia_connect_api.Token import Token
from hyundai_kia_connect_api.Vehicle import Vehicle


def _make_base_token() -> Token:
    """A valid CCI token for _gspa_get tests."""
    return Token(
        username="user@test.com",
        password="MyPassword123!",
        access_token="Bearer ccs-token",
        refresh_token="REFRESHTOKEN1234567890123456789012345678901234567890",
        device_id="12345678-1234-1234-1234-123456789abc",
        valid_until=dt.datetime.now(dt.UTC) + dt.timedelta(hours=1),
        user_id="test-uid-123",
    )


def _make_base_vehicle() -> Vehicle:
    vehicle = Vehicle()
    vehicle.id = "test123"
    return vehicle


def test_hyundai_device_id_header():
    """Hyundai uses X-Device-Id (§3.1: Hyundai EU=false)."""
    api = HyundaiCciApiEU(9, 2, "en")
    assert api.DEVICE_ID_HEADER == "X-Device-Id"


def test_hyundai_request_id_header():
    """Hyundai uses X-Request-Id."""
    api = HyundaiCciApiEU(9, 2, "en")
    assert api.REQUEST_ID_HEADER == "X-Request-Id"


def test_hyundai_cipher_brand():
    """Hyundai uses hyundai cipher."""
    api = HyundaiCciApiEU(9, 2, "en")
    assert api.CIPHER_BRAND == "hyundai"


def test_hyundai_ccsp_api_url_derived():
    """CCSP_API_URL derived from GSPA_BASE_URL (no trailing slash)."""
    api = HyundaiCciApiEU(9, 2, "en")
    assert api.CCSP_API_URL == "https://gspa-ccs-eu.hyundai.com"


def test_base_stamp_uses_brand_cipher():
    """_get_stamp delegates to the brand-specific cipher instance."""
    api = HyundaiCciApiEU(9, 2, "en")
    assert api._cipher is not None
    # Stamp is base64 and non-empty for valid inputs
    stamp = api._cipher.compute_x_stamp(
        region=1, tsid="AAAAAAAAAAAAAAAA", epoch_seconds=1700000000, user_id="u1"
    )
    assert len(stamp) > 0


def test_base_cci_domain_api_url():
    """CCI_DOMAIN_API_URL is derived from CCI_API_URL."""
    api = HyundaiCciApiEU(9, 2, "en")
    assert api.CCI_DOMAIN_API_URL == "https://cci-api-eu.hyundai.com/domain/api/"


@pytest.mark.parametrize("not_ev", [False, 0, "0", "N", "false", "no", None])
def test_parse_vehicles_does_not_treat_false_like_is_ev_values_as_true(not_ev):
    api = HyundaiCciApiEU(9, 2, "en")

    vehicle = api._parse_vehicles_from_cci(
        {"contents": [{"vehicleId": "car-id", "isEv": not_ev}]}
    )[0]

    assert vehicle.engine_type == ENGINE_TYPES.ICE


@pytest.mark.parametrize("is_ev", [True, 1, "1", "Y", "true", "yes"])
def test_parse_vehicles_accepts_boolean_like_is_ev_values(is_ev):
    api = HyundaiCciApiEU(9, 2, "en")

    vehicle = api._parse_vehicles_from_cci(
        {"contents": [{"vehicleId": "car-id", "isEv": is_ev}]}
    )[0]

    assert vehicle.engine_type == ENGINE_TYPES.EV


def test_parse_vehicles_does_not_infer_ccs2_from_false_like_strings():
    api = HyundaiCciApiEU(9, 2, "en")

    vehicle = api._parse_vehicles_from_cci(
        {
            "contents": [
                {
                    "vehicleId": "car-id",
                    "isCcs": "N",
                    "isCcsOpen": "N",
                }
            ]
        }
    )[0]

    assert vehicle.ccu_ccs2_protocol_support == 0


def test_base_gspa_get_url_construction():
    """_gspa_get requests CCSP_API_URL + /gspa/v1/{endpoint} (carId
    substituted) — verified against the actual requests.get call."""
    api = HyundaiCciApiEU(9, 2, "en")
    endpoint = "status/vehicles/{carId}/stored-status-widget"
    car_id = "test123"

    token = _make_base_token()
    vehicle = _make_base_vehicle()

    resp = MagicMock(status_code=200)
    resp.json.return_value = {"metaInfo": {"retCode": "S"}, "data": {}}
    with patch(
        "hyundai_kia_connect_api.GspaApiEU.requests.get", return_value=resp
    ) as mock_get:
        api._gspa_get(token, vehicle, endpoint)

    assert mock_get.call_args[0][0] == (
        api.CCSP_API_URL + f"/gspa/v1/{endpoint.format(carId=car_id)}"
    )


def test_base_gspa_get_waf_403_html_raises_api_error():
    """A WAF-style 403 with an HTML body raises APIError with a
    non-JSON body preview — not a raw JSONDecodeError (I1 fix)."""
    api = HyundaiCciApiEU(9, 2, "en")
    token = _make_base_token()
    vehicle = _make_base_vehicle()

    resp = MagicMock(status_code=403)
    resp.text = "<html>Access Denied — WAF block</html>"
    resp.json.side_effect = json.JSONDecodeError("Expecting value", "<html>", 0)
    with (
        patch("hyundai_kia_connect_api.GspaApiEU.requests.get", return_value=resp),
        pytest.raises(APIError, match="non-JSON body:"),
    ):
        api._gspa_get(token, vehicle, "status/vehicles/{carId}/stored-status")


def test_base_gspa_get_403_json_meta_raises_auth_error():
    """A 403 with a JSON metaInfo envelope raises APIError carrying the
    resCode and message (I1 fix — the 403 branch is no longer dead)."""
    api = HyundaiCciApiEU(9, 2, "en")
    token = _make_base_token()
    vehicle = _make_base_vehicle()

    resp = MagicMock(status_code=403)
    resp.json.return_value = {
        "metaInfo": {"retCode": None, "resCode": "403-000", "message": "Blocked"},
    }
    with (
        patch("hyundai_kia_connect_api.GspaApiEU.requests.get", return_value=resp),
        pytest.raises(APIError, match="GSPA auth error: 403-000 Blocked"),
    ):
        api._gspa_get(token, vehicle, "status/vehicles/{carId}/stored-status")


def test_base_gspa_get_500_json_meta_raises_api_error():
    """A >=400 JSON response raises APIError including the resCode."""
    api = HyundaiCciApiEU(9, 2, "en")
    token = _make_base_token()
    vehicle = _make_base_vehicle()

    resp = MagicMock(status_code=500)
    resp.json.return_value = {
        "metaInfo": {"retCode": None, "resCode": "500-999", "message": "Boom"},
    }
    with (
        patch("hyundai_kia_connect_api.GspaApiEU.requests.get", return_value=resp),
        pytest.raises(APIError, match="HTTP 500 500-999"),
    ):
        api._gspa_get(token, vehicle, "status/vehicles/{carId}/stored-status")
