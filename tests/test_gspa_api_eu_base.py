"""GspaApiEU base — header builder, stamp delegation, and _gspa_get tests."""

import datetime as dt
import json
from unittest.mock import MagicMock, patch

import pytest

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


def test_spa_api_url_built_from_ccapi_base():
    """The legacy /tripinfo host is derived from CCAPI_BASE_URL."""
    api = HyundaiCciApiEU(9, 2, "en")
    assert api.SPA_API_URL == "https://prd.eu-ccapi.hyundai.com:8080/api/v1/spa/"


def _trip_token() -> Token:
    return _make_base_token()


def test_update_month_trip_info_parses():
    api = HyundaiCciApiEU(9, 2, "en")
    token = _trip_token()
    vehicle = _make_base_vehicle()

    resp = MagicMock()
    resp.json.return_value = {
        "retCode": "S",
        "resMsg": {
            "monthTripDayCnt": 2,
            "tripDrvTime": 100,
            "tripIdleTime": 20,
            "tripDist": 150.5,
            "tripAvgSpeed": 40,
            "tripMaxSpeed": 90,
            "tripDayList": [
                {"tripDayInMonth": 5, "tripCntDay": 2},
                {"tripDayInMonth": 6, "tripCntDay": 3},
            ],
        },
    }
    with patch(
        "hyundai_kia_connect_api.GspaApiEU.requests.post", return_value=resp
    ) as mock_post:
        api.update_month_trip_info(token, vehicle, "202608")

    call_url = mock_post.call_args[0][0]
    assert call_url == (
        "https://prd.eu-ccapi.hyundai.com:8080/api/v1/spa/vehicles/test123/tripinfo"
    )
    assert mock_post.call_args[1]["json"] == {
        "tripPeriodType": 0,
        "setTripMonth": "202608",
    }
    info = vehicle.month_trip_info
    assert info is not None
    assert info.yyyymm == "202608"
    assert info.summary.drive_time == 100
    assert info.summary.distance == 150.5
    assert [d.yyyymmdd for d in info.day_list] == [5, 6]
    assert [d.trip_count for d in info.day_list] == [2, 3]


def test_update_month_trip_info_empty_sets_none():
    api = HyundaiCciApiEU(9, 2, "en")
    token = _trip_token()
    vehicle = _make_base_vehicle()
    resp = MagicMock()
    resp.json.return_value = {"retCode": "S", "resMsg": {"monthTripDayCnt": 0}}
    with patch("hyundai_kia_connect_api.GspaApiEU.requests.post", return_value=resp):
        api.update_month_trip_info(token, vehicle, "202608")
    assert vehicle.month_trip_info is None


def test_update_day_trip_info_parses():
    api = HyundaiCciApiEU(9, 2, "en")
    token = _trip_token()
    vehicle = _make_base_vehicle()
    resp = MagicMock()
    resp.json.return_value = {
        "retCode": "S",
        "resMsg": {
            "dayTripList": [
                {
                    "tripDrvTime": 30,
                    "tripIdleTime": 5,
                    "tripDist": 12.3,
                    "tripAvgSpeed": 25,
                    "tripMaxSpeed": 60,
                    "tripList": [
                        {
                            "tripTime": "081230",
                            "tripDrvTime": 10,
                            "tripIdleTime": 1,
                            "tripDist": 4.5,
                            "tripAvgSpeed": 20,
                            "tripMaxSpeed": 45,
                        }
                    ],
                }
            ]
        },
    }
    with patch("hyundai_kia_connect_api.GspaApiEU.requests.post", return_value=resp):
        api.update_day_trip_info(token, vehicle, "20260904")

    info = vehicle.day_trip_info
    assert info is not None
    assert info.yyyymmdd == "20260904"
    assert info.summary.drive_time == 30
    assert info.trip_list[0].hhmmss == "081230"
    assert info.trip_list[0].distance == 4.5


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
