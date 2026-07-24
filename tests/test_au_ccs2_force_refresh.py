"""AU CCS2 force_refresh uses base impl (state.Vehicle + Date UTC + park location)."""

import datetime as dt
from unittest.mock import MagicMock, patch

import pytest

from hyundai_kia_connect_api.KiaUvoApiAU import KiaUvoApiAU
from hyundai_kia_connect_api.Token import Token
from hyundai_kia_connect_api.Vehicle import Vehicle
from hyundai_kia_connect_api.const import DISTANCE_UNITS, ENGINE_TYPES


@pytest.fixture
def au_api():
    return KiaUvoApiAU(region=2, brand=2, language="en")


@pytest.fixture
def ccs2_vehicle():
    v = Vehicle()
    v.id = "test-vehicle-id"
    v.engine_type = ENGINE_TYPES.PHEV
    v.ccu_ccs2_protocol_support = 1
    v.distance_unit = DISTANCE_UNITS[1]
    return v


@pytest.fixture
def token():
    t = Token()
    t.access_token = "Bearer test"
    t.pin = "1234"
    t.device_id = "dev"
    return t


def test_au_force_refresh_uses_base_hooks(au_api, token, ccs2_vehicle):
    """AU inherits base _inspect + _apply + _post_refresh hooks (no override).

    Before override removal: AU's stale _force_refresh_vehicle_state_ccs2 calls
    _update_vehicle_properties_ccs2 directly and inlines location, so the base
    hooks _apply_ccs2_state / _post_refresh_ccs2_location are NOT called.
    After removal: base impl calls both hooks once each.
    """
    fresh_date = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=10)).strftime(
        "%Y%m%d%H%M%S"
    )

    def mock_get(url, **kwargs):
        resp = MagicMock()
        if url.endswith("/ccs2/carstatus") and not url.endswith("/latest"):
            resp.json.return_value = {"retCode": "S", "resCode": "0000"}
        else:
            resp.json.return_value = {
                "retCode": "S",
                "resMsg": {"state": {"Vehicle": {"Date": fresh_date}}},
            }
        return resp

    with (
        patch.object(au_api.session, "get", side_effect=mock_get),
        patch("hyundai_kia_connect_api.ApiImplType1.sleep"),
        # create=True so the patch works both while AU still imports sleep
        # (override path) and after the import is removed (base path).
        patch("hyundai_kia_connect_api.KiaUvoApiAU.sleep", create=True),
        patch.object(au_api, "_apply_ccs2_state") as mock_apply,
        patch.object(au_api, "_update_vehicle_properties_ccs2"),
        patch.object(au_api, "_post_refresh_ccs2_location") as mock_loc,
    ):
        au_api._force_refresh_vehicle_state_ccs2(token, ccs2_vehicle)

    # Base impl calls _apply_ccs2_state (which delegates to _update_vehicle_properties_ccs2).
    # The stale AU override called _update_vehicle_properties_ccs2 directly, bypassing
    # the _apply hook — so mock_apply.assert_called_once() is the RED/GREEN signal.
    mock_apply.assert_called_once()
    mock_loc.assert_called_once()


def test_au_post_refresh_location_uses_base_get_location(au_api, token, ccs2_vehicle):
    """AU inherits base _post_refresh_ccs2_location: _get_location + inline set.

    Span-task coverage: the base default _post_refresh_ccs2_location lost its
    direct test when EU overrode it (Task 4). AU inherits the base default, so
    this test verifies _get_location is called and vehicle.location is set from
    its coord.lat / coord.lon / time fields.
    """
    loc_time = "20260701120000"
    loc_response = {
        "coord": {"lat": -33.86, "lon": 151.21},
        "time": loc_time,
    }

    with patch.object(au_api, "_get_location", return_value=loc_response):
        au_api._post_refresh_ccs2_location(token, ccs2_vehicle)

    expected_lat = -33.86
    expected_lon = 151.21
    expected_time = dt.datetime(2026, 7, 1, 12, 0, 0, tzinfo=au_api.data_timezone)
    # Vehicle.location getter returns (lon, lat); setter takes (lat, lon, time)
    assert ccs2_vehicle.location == (expected_lon, expected_lat)
    assert ccs2_vehicle.location_latitude == expected_lat
    assert ccs2_vehicle.location_longitude == expected_lon
    assert ccs2_vehicle.location_last_updated_at == expected_time
