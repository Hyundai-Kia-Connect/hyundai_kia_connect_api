"""GspaApiEU base — header builder, stamp delegation, and _gspa_get tests."""

import datetime as dt
import json
from unittest.mock import MagicMock, patch

import pytest

from hyundai_kia_connect_api.exceptions import APIError, DeviceIDError
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


def test_spa_api_url_built_from_ccapi_base():
    """The legacy /tripinfo host is derived from CCAPI_BASE_URL."""
    api = HyundaiCciApiEU(9, 2, "en")
    assert api.SPA_API_URL == "https://prd.eu-ccapi.hyundai.com:8080/api/v1/spa/"


def test_update_month_trip_info_parses():
    api = HyundaiCciApiEU(9, 2, "en")
    token = _make_base_token()
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
    token = _make_base_token()
    vehicle = _make_base_vehicle()
    resp = MagicMock()
    resp.json.return_value = {"retCode": "S", "resMsg": {"monthTripDayCnt": 0}}
    with patch("hyundai_kia_connect_api.GspaApiEU.requests.post", return_value=resp):
        api.update_month_trip_info(token, vehicle, "202608")
    assert vehicle.month_trip_info is None


def test_update_day_trip_info_parses():
    api = HyundaiCciApiEU(9, 2, "en")
    token = _make_base_token()
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


# ---------------------------------------------------------------------------
# Legacy v1 device registration (D7 — live-proven 2026-09-30)
# ---------------------------------------------------------------------------


def _legacy_register_response_ok(device_id: str = "canonical-device-id") -> MagicMock:
    resp = MagicMock()
    resp.json.return_value = {
        "retCode": "S",
        "resCode": "0000",
        "resMsg": {"deviceId": device_id},
    }
    return resp


def test_register_legacy_device_hyundai_live_shape():
    """Legacy register posts to the :8080 SPA host, GCM body, legacy headers.

    Live shape (2026-09-30): POST
    https://prd.eu-ccapi.hyundai.com:8080/api/v1/spa/notifications/register
    with ccsp-service-id / ccsp-application-id / Stamp headers and
    {pushRegId, pushType, uuid} body — no Authorization. Hyundai legacy
    push type is GCM (APNS is rejected with 4002).
    """
    api = HyundaiCciApiEU(9, 2, "en")
    with patch(
        "hyundai_kia_connect_api.GspaApiEU.requests.post",
        return_value=_legacy_register_response_ok(),
    ) as mock_post:
        device_id = api._register_legacy_device()

    assert device_id == "canonical-device-id"
    url = mock_post.call_args[0][0]
    assert url == (
        "https://prd.eu-ccapi.hyundai.com:8080/api/v1/spa/notifications/register"
    )
    body = mock_post.call_args[1]["json"]
    assert body["pushType"] == "GCM"
    assert len(body["pushRegId"]) == 64
    assert body["uuid"]
    headers = mock_post.call_args[1]["headers"]
    assert headers["ccsp-service-id"] == ("6d477c38-3ca4-4cf3-9557-2a1929a94654")
    assert headers["ccsp-application-id"] == "014d2225-8495-4735-812d-2616334fd15d"
    assert headers["Stamp"]
    assert "Authorization" not in headers


def test_register_legacy_device_kia_live_shape():
    """Kia legacy register uses its own constants and APNS push type."""
    api = KiaCciApiEU(9, 1, "en")
    with patch(
        "hyundai_kia_connect_api.GspaApiEU.requests.post",
        return_value=_legacy_register_response_ok(),
    ) as mock_post:
        device_id = api._register_legacy_device()

    assert device_id == "canonical-device-id"
    assert mock_post.call_args[0][0] == (
        "https://prd.eu-ccapi.kia.com:8080/api/v1/spa/notifications/register"
    )
    body = mock_post.call_args[1]["json"]
    assert body["pushType"] == "APNS"
    headers = mock_post.call_args[1]["headers"]
    assert headers["ccsp-service-id"] == "fdc85c00-0a2f-4c64-bcb4-2cfb1500730a"
    assert headers["ccsp-application-id"] == "a2b8469b-30a3-4361-8e13-6fceea8fbe74"


def test_register_legacy_device_error_raises_device_id_error():
    """A legacy error response (4002) raises DeviceIDError."""
    api = HyundaiCciApiEU(9, 2, "en")
    resp = MagicMock()
    resp.json.return_value = {
        "retCode": "F",
        "resCode": "4002",
        "resMsg": "Invalid request body - Invalid parameter.",
    }
    with (
        patch("hyundai_kia_connect_api.GspaApiEU.requests.post", return_value=resp),
        pytest.raises(DeviceIDError),
    ):
        api._register_legacy_device()


def test_login_uses_legacy_registered_device_id():
    """login() carries the legacy-registered device_id into the Token.

    The CCI password login must use the same device_id (client-device-id),
    so one canonical id serves both the legacy v1 host and CCI/GSPA.
    """
    api = HyundaiCciApiEU(9, 2, "en")
    login_result = {
        "access_token": "Bearer ccs-token",
        "refresh_token": "REFRESH123456789012345678901234567890",
        "valid_until": dt.datetime.now(dt.UTC) + dt.timedelta(hours=1),
        "cci_access_token": "cci-token",
    }
    with (
        patch.object(
            api, "_register_legacy_device", return_value="legacy-registered-id"
        ) as mock_register,
        patch.object(
            api, "_login_with_password", return_value=login_result
        ) as mock_login,
        patch.object(api, "_register_device"),
        patch.object(api, "_fetch_user_id"),
    ):
        token = api.login("user@test.com", "MyPassword123!")

    assert token.device_id == "legacy-registered-id"
    mock_register.assert_called_once()
    assert mock_login.call_args[0][2] == "legacy-registered-id"
