"""EU forced refresh: GET /location may return the location fields flat under
resMsg, without the gpsDetail wrapper (#1301).

Some EU non-CCS2 accounts (Kia Sorento 2021 reported in #1301) return
resMsg.{coord, head, speed, accuracy, time} directly, the same flat shape GET
/location/park already parses. The old code read only resMsg.gpsDetail and
silently dropped the position.
"""

import datetime as dt
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from hyundai_kia_connect_api.KiaUvoApiEU import KiaUvoApiEU
from hyundai_kia_connect_api.Vehicle import Vehicle

# Raw response shape from #1301: the fields sit directly in resMsg.
FLAT_LOCATION = {
    "retCode": "S",
    "resCode": "0000",
    "resMsg": {
        "coord": {"lat": 52.52, "lon": 13.405, "alt": 0, "type": 0},
        "head": 127,
        "speed": {"value": 0, "unit": 0},
        "accuracy": {"hdop": 0, "pdop": 0},
        "time": "20260903105844",
    },
}
WRAPPED_LOCATION = {
    "retCode": "S",
    "resCode": "0000",
    "resMsg": {
        "gpsDetail": {
            "coord": {"lat": 48.85, "lon": 2.35},
            "time": "20260903110000",
        }
    },
}
FORCED_STATUS = {"retCode": "S", "resCode": "0000", "resMsg": {"doorLock": True}}


@pytest.fixture
def eu_api() -> KiaUvoApiEU:
    api = KiaUvoApiEU.__new__(KiaUvoApiEU)
    api.SPA_API_URL = "https://test.invalid/api/v1/spa/"
    api.data_timezone = KiaUvoApiEU.data_timezone
    api.temperature_range = KiaUvoApiEU.temperature_range
    api.session = MagicMock()
    api._get_authenticated_headers = MagicMock(
        return_value={"Authorization": "Bearer x"}
    )
    return api


@pytest.fixture
def vehicle() -> Vehicle:
    v = Vehicle()
    v.id = "vid-123"
    v.ccu_ccs2_protocol_support = 0
    return v


def _mock_get(status, location):
    def get(url, headers=None):
        resp = MagicMock()
        resp.json.return_value = location if url.endswith("/location") else status
        return resp

    return get


def test_flat_resmsg_location_is_applied(eu_api, vehicle):
    token = SimpleNamespace(access_token="t", device_id="d")
    with patch.object(
        eu_api.session, "get", side_effect=_mock_get(FORCED_STATUS, FLAT_LOCATION)
    ):
        eu_api.force_refresh_vehicle_state(token, vehicle)

    assert vehicle.location_latitude == 52.52
    assert vehicle.location_longitude == 13.405
    # The flat shape feeds the same gpsDetail clock: UTC, converted like
    # the wrapped shape (#1292).
    assert vehicle.location_last_updated_at == dt.datetime(
        2026, 9, 3, 10, 58, 44, tzinfo=dt.UTC
    )


def test_wrapped_gps_detail_takes_precedence(eu_api, vehicle):
    """Accounts that do return the wrapper must not change behavior."""
    location = WRAPPED_LOCATION
    token = SimpleNamespace(access_token="t", device_id="d")
    with patch.object(
        eu_api.session, "get", side_effect=_mock_get(FORCED_STATUS, location)
    ):
        eu_api.force_refresh_vehicle_state(token, vehicle)

    assert vehicle.location_latitude == 48.85
    assert vehicle.location_longitude == 2.35
    assert vehicle.location_last_updated_at == dt.datetime(
        2026, 9, 3, 11, 0, 0, tzinfo=dt.UTC
    )


def test_resmsg_without_coord_does_not_become_location(eu_api, vehicle):
    """No gpsDetail and no coord is still the offline/partial case: the flat
    fallback hands resMsg over, but the coord guard in
    _set_location_from_gps_detail keeps the previously known position."""
    cached = dt.datetime(2026, 9, 3, 8, 0, 0, tzinfo=KiaUvoApiEU.data_timezone)
    vehicle.location = (52.52, 13.405, cached)
    location = {
        "retCode": "S",
        "resCode": "0000",
        "resMsg": {"head": 127},
    }
    token = SimpleNamespace(access_token="t", device_id="d")
    with patch.object(
        eu_api.session, "get", side_effect=_mock_get(FORCED_STATUS, location)
    ):
        eu_api.force_refresh_vehicle_state(token, vehicle)

    assert vehicle.location_latitude == 52.52
    assert vehicle.location_last_updated_at == cached
