"""GSPA control infra — maps, error classifier, control token, envelope."""

import datetime as dt

import pytest

from hyundai_kia_connect_api.exceptions import (
    APIError,
    AuthenticationError,
    DuplicateRequestError,
    ServiceTemporaryUnavailable,
    UnsupportedControlError,
)
from hyundai_kia_connect_api.GspaApiEU import GspaApiEU
from hyundai_kia_connect_api.HyundaiCciApiEU import HyundaiCciApiEU
from hyundai_kia_connect_api.Token import Token
from hyundai_kia_connect_api.Vehicle import Vehicle


def _make_api() -> HyundaiCciApiEU:
    return HyundaiCciApiEU(9, 2, "en")


def _make_token() -> Token:
    return Token(
        username="user@test.com",
        password="MyPassword123!",
        pin="1234",
        access_token="Bearer ccs-token",
        refresh_token="REFRESHTOKEN1234567890123456789012345678901234567890",
        device_id="12345678-1234-1234-1234-123456789abc",
        valid_until=dt.datetime.now(dt.UTC) + dt.timedelta(hours=1),
        user_id="test-uid-123",
    )


def _make_vehicle(ccs2: int = 1) -> Vehicle:
    vehicle = Vehicle()
    vehicle.id = "test123"
    vehicle.ccu_ccs2_protocol_support = ccs2
    return vehicle


def test_endpoint_map():
    assert GspaApiEU.GSPA_ENDPOINT_MAP["hornlight"] == "horn-light"
    assert GspaApiEU.GSPA_ENDPOINT_MAP["windowcurtain"] == "window-curtain"


def test_path_prefix_map():
    assert GspaApiEU.GSPA_PATH_PREFIX_MAP["valet"] == "valet/vehicles"
    assert GspaApiEU.GSPA_PATH_PREFIX_MAP["rearseat-alarm"] == "safety/vehicles"


def test_bearer_endpoints_cover_settings_and_reservations():
    assert "charge-target" in GspaApiEU.GSPA_BEARER_ENDPOINTS
    assert "lock-and-start-toggle" in GspaApiEU.GSPA_BEARER_ENDPOINTS
    assert "door" not in GspaApiEU.GSPA_BEARER_ENDPOINTS


def test_remote_vehicles_path():
    assert GspaApiEU.GSPA_REMOTE_VEHICLES_PATH == "gspa/v1/remote/vehicles"


def test_raise_gspa_error_401():
    with pytest.raises(AuthenticationError):
        _make_api()._raise_gspa_error(401, {})


def test_raise_gspa_error_duplicate():
    api = _make_api()
    for rc in ("400-004", "4004"):
        with pytest.raises(DuplicateRequestError):
            api._raise_gspa_error(200, {"rc": rc, "msg": "dup"})


def test_raise_gspa_error_403():
    with pytest.raises(AuthenticationError):
        _make_api()._raise_gspa_error(403, {"rc": "403-101", "msg": "stamp"})


def test_raise_gspa_error_404():
    with pytest.raises(UnsupportedControlError):
        _make_api()._raise_gspa_error(404, {"rc": "404-999", "msg": "nope"})


def test_raise_gspa_error_no_update_info_is_api_error():
    with pytest.raises(APIError, match="No pending OTA"):
        _make_api()._raise_gspa_error(
            404, {"rc": "404-007", "msg": "No update info found by vin"}
        )


def test_raise_gspa_error_5xx():
    api = _make_api()
    with pytest.raises(ServiceTemporaryUnavailable):
        api._raise_gspa_error(503, {})
    with pytest.raises(ServiceTemporaryUnavailable):
        api._raise_gspa_error(200, {"rc": "500-001", "msg": "boom"})


def test_raise_gspa_error_unknown_is_api_error_with_server_msg():
    with pytest.raises(APIError, match="weird business failure"):
        _make_api()._raise_gspa_error(
            400, {"rc": "400-999", "msg": "weird business failure"}
        )
