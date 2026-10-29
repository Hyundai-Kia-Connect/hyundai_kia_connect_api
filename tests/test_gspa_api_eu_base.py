"""GspaApiEU base — header builder, stamp delegation, and _gspa_get tests."""

import datetime as dt
import json
from unittest.mock import MagicMock, patch

import pytest

from hyundai_kia_connect_api.exceptions import APIError, AuthenticationError
from hyundai_kia_connect_api.GspaApiEU import _parse_gspa_svm_timestamp
from hyundai_kia_connect_api.HyundaiCciApiEU import HyundaiCciApiEU
from hyundai_kia_connect_api.KiaCciApiEU import KiaCciApiEU
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
    assert api._get_cipher() is not None  # lazy: first use = load
    # Stamp is base64 and non-empty for valid inputs
    stamp = api._get_cipher().compute_x_stamp(
        region=1, tsid="AAAAAAAAAAAAAAAA", epoch_seconds=1700000000, user_id="u1"
    )
    assert len(stamp) > 0


def test_base_cci_domain_api_url():
    """CCI_DOMAIN_API_URL is derived from CCI_API_URL."""
    api = HyundaiCciApiEU(9, 2, "en")
    assert api.CCI_DOMAIN_API_URL == "https://cci-api-eu.hyundai.com/domain/api/"


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


EXTENDED_READS = {
    "get_location_update_status": "location/vehicles/test123/update-status",
    "get_location_routes": "location/vehicles/test123/routes",
    "get_valet_status": "valet/vehicles/test123/status",
    "get_valet_history": "valet/vehicles/test123/history",
    "get_safety_data": "safety/vehicles/test123/alert-setting",
    "get_stored_status_widget": "status/vehicles/test123/stored-status-widget",
}


@pytest.mark.parametrize(
    "method,path",
    sorted(EXTENDED_READS.items()),
    ids=[name for name, _ in sorted(EXTENDED_READS.items())],
)
def test_extended_read_url_construction(method, path):
    """Each extended read requests CCSP_API_URL + /gspa/v1/{path}."""
    api = HyundaiCciApiEU(9, 2, "en")
    token = _make_base_token()
    vehicle = _make_base_vehicle()

    resp = MagicMock(status_code=200)
    resp.json.return_value = {"metaInfo": {"retCode": "S"}, "data": {}}
    with patch(
        "hyundai_kia_connect_api.GspaApiEU.requests.get", return_value=resp
    ) as mock_get:
        getattr(api, method)(token, vehicle)

    assert mock_get.call_args[0][0] == api.CCSP_API_URL + f"/gspa/v1/{path}"


def test_get_gspa_vehicles_url_and_payload():
    """get_gspa_vehicles GETs /gspa/v1/vehicles (no carId) and returns the
    data list."""
    api = HyundaiCciApiEU(9, 2, "en")
    token = _make_base_token()

    vehicles = [{"vin": "VIN1"}, {"vin": "VIN2"}]
    resp = MagicMock(status_code=200)
    resp.json.return_value = {
        "metaInfo": {"retCode": "S", "resCode": "200-000"},
        "data": vehicles,
    }
    with patch(
        "hyundai_kia_connect_api.GspaApiEU.requests.get", return_value=resp
    ) as mock_get:
        result = api.get_gspa_vehicles(token)

    assert result == vehicles
    assert mock_get.call_args[0][0] == api.CCSP_API_URL + "/gspa/v1/vehicles"


def test_get_gspa_vehicles_returns_none_on_business_error():
    api = HyundaiCciApiEU(9, 2, "en")
    token = _make_base_token()
    resp = MagicMock(status_code=200)
    resp.json.return_value = {
        "metaInfo": {"retCode": "F", "resCode": "404-001", "message": "none"},
        "data": None,
    }
    with patch("hyundai_kia_connect_api.GspaApiEU.requests.get", return_value=resp):
        assert api.get_gspa_vehicles(token) is None


def test_get_weather_url_and_payload():
    api = HyundaiCciApiEU(9, 2, "en")
    token = _make_base_token()
    rs_data = {"temperature": 21}
    resp = MagicMock(status_code=200)
    resp.json.return_value = {
        "metaInfo": {"retCode": "S", "resCode": "200-000"},
        "data": rs_data,
    }
    with patch(
        "hyundai_kia_connect_api.GspaApiEU.requests.get", return_value=resp
    ) as mock_get:
        result = api.get_weather(token, 50.0614, 19.9373)

    assert result == rs_data
    assert mock_get.call_args[0][0].endswith("/gspa/v1/contents/wts/weathers")
    # On-device-confirmed query shape: currentCoordinate=%f,%f + attributes
    assert mock_get.call_args[1]["params"] == {
        "currentCoordinate": "50.061400,19.937300",
        "attributes": "currentWeather",
    }


def _trip_token() -> Token:
    return _make_base_token()


def test_get_location_stored_status_path():
    """Location stored-status read — D7 companion endpoint."""
    api = HyundaiCciApiEU(9, 2, "en")
    token = _make_base_token()
    vehicle = _make_base_vehicle()

    resp = MagicMock(status_code=200)
    resp.json.return_value = {"metaInfo": {"retCode": "S"}, "data": {}}
    with patch(
        "hyundai_kia_connect_api.GspaApiEU.requests.get", return_value=resp
    ) as mock_get:
        api.get_location_stored_status(token, vehicle)

    assert mock_get.call_args[0][0] == (
        api.CCSP_API_URL + "/gspa/v1/location/vehicles/test123/stored-status"
    )


def test_manager_init_does_not_read_cipher_files(monkeypatch):
    """#1388: constructing the manager must not read the cipher JSON files —
    kia_uvo builds the manager inside async_setup_entry (event loop), so the
    read belongs to the first stamp use instead."""
    from hyundai_kia_connect_api.gspa import cipher_keys

    reads: list[str] = []
    orig = cipher_keys._load_json

    def counting(filename: str):
        reads.append(filename)
        return orig(filename)

    monkeypatch.setattr(cipher_keys, "_load_json", counting)
    # reset the module singletons so this test observes its own loads
    monkeypatch.setattr(cipher_keys, "_hyundai_cipher", None)
    monkeypatch.setattr(cipher_keys, "_kia_cipher", None)
    HyundaiCciApiEU(9, 2, "en")
    KiaCciApiEU(9, 1, "en")
    assert reads == []  # nothing read during construction

    api = HyundaiCciApiEU(9, 2, "en")
    api._get_cipher()  # first cipher use
    assert sorted(reads) == ["hyundai_cipher_params.json"]
    api._get_cipher()  # singleton: no second read
    assert sorted(reads) == ["hyundai_cipher_params.json"]


def test_parse_gspa_svm_timestamp_millis_variant():
    """Location stored-status appends .mmm milliseconds (live PV5,
    2026-10-27); the 14-digit and unparsable shapes behave as before."""
    assert _parse_gspa_svm_timestamp("20261027220812.291") == dt.datetime(
        2026, 10, 27, 22, 8, 12, 291000, tzinfo=dt.UTC
    )
    assert _parse_gspa_svm_timestamp("20261027220812") == dt.datetime(
        2026, 10, 27, 22, 8, 12, tzinfo=dt.UTC
    )
    assert _parse_gspa_svm_timestamp("2026102722.291") is None
    # right shape, invalid date — the strptime error arm stays covered
    assert _parse_gspa_svm_timestamp("20261999009999.291") is None


LOCATION_STATUS_PAYLOAD = {
    "coord": {"lat": 0.0, "lon": 0.0, "type": 0, "alt": 0.0},
    "head": 0,
    "speed": {"value": 0, "unit": 0},
    "time": "20261027220812.291",
}


def test_update_vehicle_gspa_location_sets_location():
    """No Location node in the status tree (PV5): the fallback read sets
    vehicle.location from the server-cached position with its UTC time."""
    api = HyundaiCciApiEU(9, 2, "en")
    token = _make_base_token()
    vehicle = _make_base_vehicle()

    with patch.object(
        api, "get_location_stored_status", return_value=LOCATION_STATUS_PAYLOAD
    ) as mock_read:
        api._update_vehicle_gspa_location(token, vehicle)

    mock_read.assert_called_once()
    assert vehicle.location_latitude == 0.0
    assert vehicle.location_longitude == 0.0
    assert vehicle.location_last_updated_at == dt.datetime(
        2026, 10, 27, 22, 8, 12, 291000, tzinfo=dt.UTC
    )


def test_update_vehicle_gspa_location_skips_vehicle_with_location_node():
    """Vehicles whose status tree carries a Location node take no extra
    request."""
    api = HyundaiCciApiEU(9, 2, "en")
    token = _make_base_token()
    vehicle = _make_base_vehicle()
    vehicle.location = (52.0, 20.0, dt.datetime.now(dt.UTC))

    with patch.object(api, "get_location_stored_status") as mock_read:
        api._update_vehicle_gspa_location(token, vehicle)

    mock_read.assert_not_called()
    assert vehicle.location_latitude == 52.0  # existing location untouched


def test_update_vehicle_gspa_location_unusable_payload_stays_unknown():
    """Missing coord or unparsable time leaves the location unset (HA
    convention: unknown, not masked)."""
    api = HyundaiCciApiEU(9, 2, "en")
    token = _make_base_token()
    vehicle = _make_base_vehicle()

    for payload in (None, {}, {"coord": {"lat": 1.0}}, {"time": "2026102722.291"}):
        with patch.object(api, "get_location_stored_status", return_value=payload):
            api._update_vehicle_gspa_location(token, vehicle)
        assert vehicle.location_latitude is None
        assert vehicle.location_last_updated_at is None


def test_update_vehicle_gspa_location_read_failure_stays_unknown():
    """A failing location read leaves the location unset, never stale."""
    api = HyundaiCciApiEU(9, 2, "en")
    token = _make_base_token()
    vehicle = _make_base_vehicle()

    with patch.object(api, "get_location_stored_status", side_effect=APIError("boom")):
        api._update_vehicle_gspa_location(token, vehicle)

    assert vehicle.location_latitude is None
    assert vehicle.location_last_updated_at is None


# ── GSPA read getters — error-path arms (codecov patch target) ──

extended_read_methods = sorted(list(EXTENDED_READS) + ["get_location_stored_status"])


@pytest.mark.parametrize("method", extended_read_methods)
def test_extended_read_swallows_transport_error(method):
    """A non-auth failure on a GSPA read returns None (fields stay unknown,
    no crash in the cached-update path)."""
    api = HyundaiCciApiEU(9, 2, "en")
    token = _make_base_token()
    vehicle = _make_base_vehicle()

    with patch(
        "hyundai_kia_connect_api.GspaApiEU.requests.get",
        side_effect=ConnectionError("boom"),
    ):
        assert getattr(api, method)(token, vehicle) is None


@pytest.mark.parametrize("method", extended_read_methods)
def test_extended_read_reraises_auth_error(method):
    """An expired token propagates (the caller refreshes and retries)."""
    api = HyundaiCciApiEU(9, 2, "en")
    token = _make_base_token()
    vehicle = _make_base_vehicle()

    with (
        patch(
            "hyundai_kia_connect_api.GspaApiEU.requests.get",
            side_effect=AuthenticationError("expired"),
        ),
        pytest.raises(AuthenticationError),
    ):
        getattr(api, method)(token, vehicle)


def test_get_gspa_vehicles_401_reraises_auth_error():
    api = HyundaiCciApiEU(9, 2, "en")
    token = _make_base_token()

    resp = MagicMock(status_code=401)
    with (
        patch("hyundai_kia_connect_api.GspaApiEU.requests.get", return_value=resp),
        pytest.raises(AuthenticationError),
    ):
        api.get_gspa_vehicles(token)


def test_get_gspa_vehicles_connection_error_returns_none():
    api = HyundaiCciApiEU(9, 2, "en")
    token = _make_base_token()

    with patch(
        "hyundai_kia_connect_api.GspaApiEU.requests.get",
        side_effect=ConnectionError("boom"),
    ):
        assert api.get_gspa_vehicles(token) is None


def test_get_weather_business_error_returns_none():
    """HTTP 200 with retCode "F" is a business error: None, no raise."""
    api = HyundaiCciApiEU(9, 2, "en")
    token = _make_base_token()

    resp = MagicMock(status_code=200)
    resp.json.return_value = {
        "metaInfo": {"retCode": "F", "resCode": "700-001", "message": "no weather"},
        "data": None,
    }
    with patch("hyundai_kia_connect_api.GspaApiEU.requests.get", return_value=resp):
        assert api.get_weather(token, 50.0614, 19.9373) is None


def test_get_weather_401_reraises_auth_error():
    api = HyundaiCciApiEU(9, 2, "en")
    token = _make_base_token()

    resp = MagicMock(status_code=401)
    with (
        patch("hyundai_kia_connect_api.GspaApiEU.requests.get", return_value=resp),
        pytest.raises(AuthenticationError),
    ):
        api.get_weather(token, 50.0614, 19.9373)


def test_get_weather_connection_error_returns_none():
    api = HyundaiCciApiEU(9, 2, "en")
    token = _make_base_token()

    with patch(
        "hyundai_kia_connect_api.GspaApiEU.requests.get",
        side_effect=ConnectionError("boom"),
    ):
        assert api.get_weather(token, 50.0614, 19.9373) is None
