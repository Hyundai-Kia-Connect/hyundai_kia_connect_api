"""Location poll (GSPA update-status long-poll) behavior tests.

Response bodies are redacted live captures (sleeping car in a garage,
2026-11-12): probes/gspa_location_probe_sleep2.json (poll with the 511
no-fix sentinel) and gspa_location_probe_sleep3.json (duplicate-window
rejections, poll with a valid fix). Coordinates replaced with neutral
values; the wire shape is unchanged.
"""

import datetime as dt
from unittest.mock import MagicMock, patch

from hyundai_kia_connect_api.GspaApiEU import GspaApiEU
from hyundai_kia_connect_api.HyundaiCciApiEU import HyundaiCciApiEU
from hyundai_kia_connect_api.Token import Token
from hyundai_kia_connect_api.Vehicle import Vehicle

POLL_BODY = {
    "data": {
        "coord": {"lat": 52.52, "lon": 13.405, "type": 0, "alt": 0.0},
        "head": 270,
        "speed": {"value": 511, "unit": 0},
        "time": "20261112225947",
    },
    "metaInfo": {"retCode": "S", "resCode": "200-000", "message": "Success"},
}

POLL_BODY_WITH_FIX = {
    "data": {
        "coord": {"lat": 52.52, "lon": 13.405, "type": 0, "alt": 0.0},
        "head": 270,
        "speed": {"value": 0, "unit": 0},
        "time": "20261112230153",
    },
    "metaInfo": {"retCode": "S", "resCode": "200-000", "message": "Success"},
}


def _token() -> Token:
    return Token(
        username="user@test.com",
        password="MyPassword123!",
        access_token="Bearer ccs-token",
        refresh_token="REFRESHTOKEN1234567890123456789012345678901234567890",
        device_id="12345678-1234-1234-1234-123456789abc",
        valid_until=dt.datetime.now(dt.UTC) + dt.timedelta(hours=1),
        user_id="test-uid-123",
    )


def _vehicle() -> Vehicle:
    vehicle = Vehicle(id="test-id")
    vehicle.id = "test123"
    return vehicle


def _api() -> GspaApiEU:
    return HyundaiCciApiEU(9, 2, "en")


def test_poll_parses_response_and_flags_missing_gps_fix():
    api = _api()
    resp = MagicMock(status_code=200)
    resp.json.return_value = POLL_BODY
    with patch(
        "hyundai_kia_connect_api.GspaApiEU.requests.get", return_value=resp
    ) as mock_get:
        result = api.poll_location_update_status(_token(), _vehicle())

    assert result == {
        "latitude": 52.52,
        "longitude": 13.405,
        "time_raw": "20261112225947",
        "speed": 511.0,
        "gps_fix": False,
    }
    args = mock_get.call_args
    assert args.kwargs["timeout"] == (5, 145)
    assert args[0][0].endswith("/gspa/v1/location/vehicles/test123/update-status")


def test_poll_reports_gps_fix_when_speed_is_valid():
    api = _api()
    resp = MagicMock(status_code=200)
    resp.json.return_value = POLL_BODY_WITH_FIX
    with patch("hyundai_kia_connect_api.GspaApiEU.requests.get", return_value=resp):
        result = api.poll_location_update_status(_token(), _vehicle())

    assert result is not None
    assert result["speed"] == 0.0
    assert result["gps_fix"] is True


def test_poll_returns_none_on_duplicate_window_rejection():
    """403-006 invalid request id (read/poll inside the duplicate
    window) returns None instead of raising — live capture, probe
    gspa_location_probe_sleep.json."""
    api = _api()
    resp = MagicMock(status_code=403)
    resp.json.return_value = {
        "data": {},
        "metaInfo": {
            "retCode": "F",
            "resCode": "403-006",
            "message": "Invalid request id",
        },
    }
    with patch("hyundai_kia_connect_api.GspaApiEU.requests.get", return_value=resp):
        assert api.poll_location_update_status(_token(), _vehicle()) is None


def test_poll_returns_none_on_duplicated_request():
    """400-004 Duplicated request (second poll inside the window) —
    live capture, probe gspa_location_probe4_force.json."""
    api = _api()
    resp = MagicMock(status_code=400)
    resp.json.return_value = {
        "data": {},
        "metaInfo": {
            "retCode": "F",
            "resCode": "400-004",
            "message": "Duplicated request",
            "detail": "<SENTINEL_VIN> /vhc/loc is duplicated request",
        },
    }
    with patch("hyundai_kia_connect_api.GspaApiEU.requests.get", return_value=resp):
        assert api.poll_location_update_status(_token(), _vehicle()) is None


def test_poll_returns_none_without_coordinates():
    api = _api()
    resp = MagicMock(status_code=200)
    resp.json.return_value = {"data": {"head": 270}, "metaInfo": {"retCode": "S"}}
    with patch("hyundai_kia_connect_api.GspaApiEU.requests.get", return_value=resp):
        assert api.poll_location_update_status(_token(), _vehicle()) is None
