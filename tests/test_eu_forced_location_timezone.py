"""EU forced refresh: GET /location returns gpsDetail.time in UTC. See kia_uvo #931.

The cached feeds (the vehicleLocation embedded in the status response, and
GET /location/park) return region local time, so only the forced value needs
a conversion. The last test guards that difference.
"""

import datetime as dt
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from hyundai_kia_connect_api.KiaUvoApiEU import KiaUvoApiEU
from hyundai_kia_connect_api.Vehicle import Vehicle

# One instant, two clocks: the forced /status payload reports local wall clock
# (18:18:10 CEST), GET /location reports UTC (16:18:10Z).
FORCED_STATUS = {
    "retCode": "S",
    "resCode": "0000",
    "resMsg": {"time": "20260624181810", "doorLock": True},
}
LOCATION = {
    "retCode": "S",
    "resCode": "0000",
    "resMsg": {
        "gpsDetail": {
            "coord": {"lat": 52.52, "lon": 13.405, "alt": 10, "type": 0},
            "head": 180,
            "speed": {"value": 0, "unit": 0},
            "time": "20260624161810",
        }
    },
}


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
    # ccu_ccs2_protocol_support defaults to None, and None != 0 is True, so it
    # must be set to take the legacy branch that calls _get_location.
    v.ccu_ccs2_protocol_support = 0
    return v


def _mock_get(status, location):
    def get(url, headers=None):
        resp = MagicMock()
        resp.json.return_value = location if url.endswith("/location") else status
        return resp

    return get


def test_forced_location_time_is_parsed_as_utc(eu_api, vehicle):
    token = SimpleNamespace(access_token="t", device_id="d")
    with patch.object(
        eu_api.session, "get", side_effect=_mock_get(FORCED_STATUS, LOCATION)
    ):
        eu_api.force_refresh_vehicle_state(token, vehicle)

    # Aware datetimes compare by instant, thus this is independent of the
    # timezone of the machine that runs the test.
    assert vehicle.location_last_updated_at == dt.datetime(
        2026, 6, 24, 16, 18, 10, tzinfo=dt.UTC
    )
    # It is the same moment that the status payload gives as local time.
    assert vehicle.location_last_updated_at == vehicle.last_updated_at


def test_forced_location_time_is_utc_in_winter(eu_api, vehicle):
    """CET, not CEST: a constant two hour shift must not pass this."""
    location = {
        "retCode": "S",
        "resCode": "0000",
        "resMsg": {
            "gpsDetail": {
                "coord": {"lat": 52.52, "lon": 13.405},
                "time": "20260115104500",
            }
        },
    }
    status = {"retCode": "S", "resCode": "0000", "resMsg": {"time": "20260115114500"}}
    token = SimpleNamespace(access_token="t", device_id="d")
    with patch.object(eu_api.session, "get", side_effect=_mock_get(status, location)):
        eu_api.force_refresh_vehicle_state(token, vehicle)

    assert vehicle.location_last_updated_at == dt.datetime(
        2026, 1, 15, 10, 45, 0, tzinfo=dt.UTC
    )


def test_forced_location_absent_keeps_cached_value(eu_api, vehicle):
    """An offline vehicle returns no gpsDetail. Do not fail, and do not
    overwrite the position that is already known."""
    cached = dt.datetime(2026, 6, 24, 12, 0, 0, tzinfo=KiaUvoApiEU.data_timezone)
    vehicle.location = (52.52, 13.405, cached)
    location = {"retCode": "S", "resCode": "0000", "resMsg": {}}
    token = SimpleNamespace(access_token="t", device_id="d")
    with patch.object(
        eu_api.session, "get", side_effect=_mock_get(FORCED_STATUS, location)
    ):
        eu_api.force_refresh_vehicle_state(token, vehicle)

    assert vehicle.location_latitude == 52.52
    assert vehicle.location_last_updated_at == cached


def test_forced_location_without_time_has_no_timestamp(eu_api, vehicle):
    """gpsDetail without a time must leave the timestamp None, which HA shows
    as "unknown", and not a sentinel. See kia_uvo #1771."""
    location = {
        "retCode": "S",
        "resCode": "0000",
        "resMsg": {"gpsDetail": {"coord": {"lat": 52.52, "lon": 13.405}}},
    }
    token = SimpleNamespace(access_token="t", device_id="d")
    with patch.object(
        eu_api.session, "get", side_effect=_mock_get(FORCED_STATUS, location)
    ):
        eu_api.force_refresh_vehicle_state(token, vehicle)

    assert vehicle.location_latitude == 52.52
    assert vehicle.location_last_updated_at is None


def test_cached_embedded_location_time_stays_local(eu_api, vehicle):
    """The other half of the seam: the vehicleLocation embedded in the cached
    status response is local time, and must not be converted."""
    state = {
        "vehicleStatus": {"time": "20260624181810"},
        "vehicleLocation": {
            "coord": {"lat": 52.52, "lon": 13.405},
            "time": "20260624181810",
        },
    }
    eu_api._update_vehicle_properties(vehicle, state)

    assert vehicle.location_last_updated_at == dt.datetime(
        2026, 6, 24, 18, 18, 10, tzinfo=KiaUvoApiEU.data_timezone
    )
