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


def test_kia_cached_state_follows_location_on_next_update():
    """Two cached updates on a vehicle with no Location node in the
    status tree (PV5): the second update reads the server position
    again (follow-car, live-verified by fellek, #1331 review)."""
    api = KiaCciApiEU(9, 1, "en")
    token = _make_base_token()
    vehicle = _make_base_vehicle()
    status = {"state": {"Vehicle": {"Drivetrain": {"Odometer": 12345}}}}
    first = LOCATION_STATUS_PAYLOAD | {
        "coord": {"lat": 52.0, "lon": 13.0, "type": 0, "alt": 0.0},
        "time": "20261029140000.000",
    }
    second = LOCATION_STATUS_PAYLOAD | {
        "coord": {"lat": 52.1, "lon": 13.1, "type": 0, "alt": 0.0},
        "time": "20261029134000.000",
    }

    with (
        patch.object(api, "get_stored_status", return_value=status),
        patch.object(
            api, "_get_location_stored_status", side_effect=[first, second]
        ) as mock_read,
    ):
        api.update_vehicle_with_cached_state(token, vehicle)
        api.update_vehicle_with_cached_state(token, vehicle)

    assert mock_read.call_count == 2
    assert vehicle.location_latitude == 52.1
    assert vehicle.location_longitude == 13.1


def test_hyundai_cached_state_skips_location_read_when_tree_has_location():
    """A status tree carrying a Location node means no extra read at the
    cached-update level."""
    api = HyundaiCciApiEU(9, 2, "en")
    token = _make_base_token()
    vehicle = _make_base_vehicle()
    status = {
        "state": {
            "Vehicle": {
                "Location": {
                    "GeoCoord": {"Latitude": 52.0, "Longitude": 20.0},
                    "TimeStamp": {
                        "Year": 2026,
                        "Mon": 10,
                        "Day": 29,
                        "Hour": 13,
                        "Min": 0,
                        "Sec": 0,
                    },
                }
            }
        }
    }

    with (
        patch.object(api, "get_stored_status", return_value=status),
        patch.object(api, "_get_location_stored_status") as mock_read,
        patch.object(api, "_update_vehicle_extras"),
    ):
        api.update_vehicle_with_cached_state(token, vehicle)

    mock_read.assert_not_called()
    assert vehicle.location_latitude == 52.0


def test_update_vehicle_gspa_location_sets_location():
    """No Location node in the status tree (PV5): the fallback read sets
    vehicle.location from the server-cached position with its UTC time."""
    api = HyundaiCciApiEU(9, 2, "en")
    token = _make_base_token()
    vehicle = _make_base_vehicle()

    with patch.object(
        api, "_get_location_stored_status", return_value=LOCATION_STATUS_PAYLOAD
    ) as mock_read:
        api._update_vehicle_gspa_location(token, vehicle)

    mock_read.assert_called_once()
    assert vehicle.location_latitude == 0.0
    assert vehicle.location_longitude == 0.0
    assert vehicle.location_last_updated_at == dt.datetime(
        2026, 10, 27, 22, 8, 12, 291000, tzinfo=dt.UTC
    )


def test_update_vehicle_gspa_location_unusable_payload_stays_unknown():
    """Missing coord or unparsable time leaves the location unset (HA
    convention: unknown, not masked)."""
    api = HyundaiCciApiEU(9, 2, "en")
    token = _make_base_token()
    vehicle = _make_base_vehicle()

    for payload in (None, {}, {"coord": {"lat": 1.0}}, {"time": "2026102722.291"}):
        with patch.object(api, "_get_location_stored_status", return_value=payload):
            api._update_vehicle_gspa_location(token, vehicle)
        assert vehicle.location_latitude is None
        assert vehicle.location_last_updated_at is None


def test_update_vehicle_gspa_location_read_failure_stays_unknown():
    """A failing location read leaves the location unset, never stale."""
    api = HyundaiCciApiEU(9, 2, "en")
    token = _make_base_token()
    vehicle = _make_base_vehicle()

    with patch.object(api, "_get_location_stored_status", side_effect=APIError("boom")):
        api._update_vehicle_gspa_location(token, vehicle)

    assert vehicle.location_latitude is None
    assert vehicle.location_last_updated_at is None


def test_get_location_stored_status_swallows_transport_error():
    """A non-auth failure on the location read returns None (fields stay
    unknown, no crash in the cached-update path)."""
    api = HyundaiCciApiEU(9, 2, "en")
    token = _make_base_token()
    vehicle = _make_base_vehicle()

    with patch(
        "hyundai_kia_connect_api.GspaApiEU.requests.get",
        side_effect=ConnectionError("boom"),
    ):
        assert api._get_location_stored_status(token, vehicle) is None


def test_get_location_stored_status_reraises_auth_error():
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
        api._get_location_stored_status(token, vehicle)


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


def test_get_ota_updates_returns_data():
    """get_ota_updates GETs the mru ota-updates path and returns the
    data payload."""
    api = HyundaiCciApiEU(9, 2, "en")
    token = _make_base_token()
    vehicle = _make_base_vehicle()

    rs_data = {"otaUpdateList": [{"updateVer": "CCU26.1.0"}]}
    resp = MagicMock(status_code=200)
    resp.json.return_value = {
        "metaInfo": {"retCode": "S", "resCode": "200-000"},
        "data": rs_data,
    }
    with patch(
        "hyundai_kia_connect_api.GspaApiEU.requests.get", return_value=resp
    ) as mock_get:
        result = api.get_ota_updates(token, vehicle)

    assert result == rs_data
    assert mock_get.call_args[0][0].endswith(
        "/gspa/v1/mru/vehicles/test123/ota-updates"
    )


def test_get_ota_updates_returns_none_on_error():
    """get_ota_updates swallows non-auth failures and returns None."""
    api = HyundaiCciApiEU(9, 2, "en")
    token = _make_base_token()
    vehicle = _make_base_vehicle()

    resp = MagicMock(status_code=200)
    resp.json.return_value = {
        "metaInfo": {"retCode": "F", "resCode": "404-007", "message": "no update info"},
        "data": None,
    }
    with patch("hyundai_kia_connect_api.GspaApiEU.requests.get", return_value=resp):
        assert api.get_ota_updates(token, vehicle) is None


def test_get_software_version_returns_data():
    """get_software_version GETs the device-info software-version path."""
    api = HyundaiCciApiEU(9, 2, "en")
    token = _make_base_token()
    vehicle = _make_base_vehicle()

    rs_data = {"swVer": "CCU25.SF.HEV.SOP1.001"}
    resp = MagicMock(status_code=200)
    resp.json.return_value = {
        "metaInfo": {"retCode": "S", "resCode": "200-000"},
        "data": rs_data,
    }
    with patch(
        "hyundai_kia_connect_api.GspaApiEU.requests.get", return_value=resp
    ) as mock_get:
        result = api.get_software_version(token, vehicle)

    assert result == rs_data
    assert mock_get.call_args[0][0].endswith(
        "/gspa/v1/device-info/vehicles/test123/software-version"
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
