"""Tests for CCS2 force_refresh_vehicle_state polling loop.

CCS2 force refresh: trigger /ccs2/carstatus, then poll /latest
until Date > trigger_time, max 10 attempts × 2s.
"""

import pytest
from unittest.mock import MagicMock, patch

from hyundai_kia_connect_api.KiaUvoApiEU import KiaUvoApiEU
from hyundai_kia_connect_api.Token import Token
from hyundai_kia_connect_api.Vehicle import Vehicle
from hyundai_kia_connect_api.const import DISTANCE_UNITS, ENGINE_TYPES


@pytest.fixture
def eu_api():
    return KiaUvoApiEU(region=1, brand=2, language="en")


@pytest.fixture
def ccs2_vehicle():
    v = Vehicle()
    v.id = "test-vehicle-id"
    v.engine_type = ENGINE_TYPES.PHEV
    v.ccu_ccs2_protocol_support = 1
    v.distance_unit = DISTANCE_UNITS[1]  # km
    return v


@pytest.fixture
def token():
    t = Token()
    t.access_token = "Bearer test-access-token"
    t.pin = "1234"
    t.device_id = "test-device-id"
    return t


def test_force_refresh_polls_until_fresh(eu_api, token, ccs2_vehicle):
    """Trigger + poll until last_updated > trigger_time, then apply."""
    import datetime as dt

    fresh_date = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=10)).strftime(
        "%Y%m%d%H%M%S"
    )
    call_log = []

    def mock_get(url, **kwargs):
        resp = MagicMock()
        if url.endswith("/ccs2/carstatus") and not url.endswith("/latest"):
            call_log.append(("trigger", url))
            resp.json.return_value = {"retCode": "S", "resCode": "0000"}
        else:
            call_log.append(("poll", url))
            resp.json.return_value = {
                "retCode": "S",
                "resMsg": {"state": {"Vehicle": {"Date": fresh_date}}},
            }
        return resp

    with (
        patch.object(eu_api.session, "get", side_effect=mock_get),
        patch("hyundai_kia_connect_api.ApiImplType1.sleep"),
        patch.object(eu_api, "_apply_ccs2_state") as mock_apply,
        patch.object(eu_api, "_post_refresh_ccs2_location") as mock_loc,
    ):
        eu_api._force_refresh_vehicle_state_ccs2(token, ccs2_vehicle)

    assert len([c for c in call_log if c[0] == "trigger"]) == 1
    assert len([c for c in call_log if c[0] == "poll"]) >= 1
    mock_apply.assert_called_once()
    mock_loc.assert_called_once()


def test_force_refresh_timeout_keeps_previous_state(eu_api, token, ccs2_vehicle):
    """On timeout (all polls stale), _apply_ccs2_state NOT called, state untouched."""
    stale_date = "20200101120000"  # past -> never > trigger_time
    ccs2_vehicle.odometer = (12345, 1)  # pre-existing state

    def mock_get(url, **kwargs):
        resp = MagicMock()
        if url.endswith("/ccs2/carstatus") and not url.endswith("/latest"):
            resp.json.return_value = {"retCode": "S", "resCode": "0000"}
        else:
            resp.json.return_value = {
                "retCode": "S",
                "resMsg": {"state": {"Vehicle": {"Date": stale_date}}},
            }
        return resp

    with (
        patch.object(eu_api.session, "get", side_effect=mock_get),
        patch("hyundai_kia_connect_api.ApiImplType1.sleep"),
        patch.object(eu_api, "_apply_ccs2_state") as mock_apply,
        patch.object(eu_api, "_post_refresh_ccs2_location") as mock_loc,
    ):
        eu_api._force_refresh_vehicle_state_ccs2(token, ccs2_vehicle)

    mock_apply.assert_not_called()  # HA: do not mask stale
    mock_loc.assert_not_called()
    assert ccs2_vehicle.odometer == 12345  # untouched


def test_force_refresh_budget_14_polls_10s(eu_api, token, ccs2_vehicle):
    """Budget is 14 polls × 10s (~140s window, matches app controlRetrofit
    readTimeout = backend max wake-to-report)."""
    stale_date = "20200101120000"
    sleep_calls = []

    def mock_get(url, **kwargs):
        resp = MagicMock()
        if url.endswith("/ccs2/carstatus") and not url.endswith("/latest"):
            resp.json.return_value = {"retCode": "S", "resCode": "0000"}
        else:
            resp.json.return_value = {
                "retCode": "S",
                "resMsg": {"state": {"Vehicle": {"Date": stale_date}}},
            }
        return resp

    def mock_sleep(secs):
        sleep_calls.append(secs)

    with (
        patch.object(eu_api.session, "get", side_effect=mock_get),
        patch("hyundai_kia_connect_api.ApiImplType1.sleep", side_effect=mock_sleep),
        patch.object(eu_api, "_apply_ccs2_state"),
        patch.object(eu_api, "_post_refresh_ccs2_location"),
    ):
        eu_api._force_refresh_vehicle_state_ccs2(token, ccs2_vehicle)

    assert len(sleep_calls) == 14
    assert all(s == 10 for s in sleep_calls)


def test_eu_post_refresh_location_uses_park_endpoint(eu_api, token, ccs2_vehicle):
    """EU _post_refresh_ccs2_location delegates to _set_cached_location_park,
    NOT base _get_location."""
    with (
        patch.object(eu_api, "_set_cached_location_park") as mock_park,
        patch.object(eu_api, "_get_location") as mock_get_loc,
    ):
        eu_api._post_refresh_ccs2_location(token, ccs2_vehicle)
    mock_park.assert_called_once_with(token, ccs2_vehicle)
    mock_get_loc.assert_not_called()


def test_inspect_ccs2_response_eu_default(eu_api):
    """Base _inspect_ccs2_response extracts state.Vehicle + Date as UTC datetime."""
    from datetime import datetime

    # Date 20260101120010 UTC = 13:00:10 CET (Europe/Berlin, +1 winter)
    response = {
        "retCode": "S",
        "resMsg": {"state": {"Vehicle": {"Date": "20260101120010"}}},
    }
    state, last_updated = eu_api._inspect_ccs2_response(response)
    assert state == {"Date": "20260101120010"}
    assert last_updated == datetime(2026, 1, 1, 13, 0, 10, tzinfo=eu_api.data_timezone)


def test_inspect_ccs2_response_missing_state(eu_api):
    """Missing resMsg.state.Vehicle -> (None, None), no exception."""
    state, last_updated = eu_api._inspect_ccs2_response({"retCode": "S"})
    assert state is None
    assert last_updated is None


def test_inspect_ccs2_response_unparseable_date(eu_api):
    """Bad Date -> (state, None), no exception, state preserved."""
    response = {
        "retCode": "S",
        "resMsg": {"state": {"Vehicle": {"Date": "not-a-date"}}},
    }
    state, last_updated = eu_api._inspect_ccs2_response(response)
    assert state == {"Date": "not-a-date"}
    assert last_updated is None


def test_inspect_ccs2_response_date_with_ms_suffix(eu_api):
    """Date '20260101120010.000' parsed same as without suffix."""
    response = {
        "retCode": "S",
        "resMsg": {"state": {"Vehicle": {"Date": "20260101120010.000"}}},
    }
    state, last_updated = eu_api._inspect_ccs2_response(response)
    assert state == {"Date": "20260101120010.000"}
    from datetime import datetime

    assert last_updated == datetime(2026, 1, 1, 13, 0, 10, tzinfo=eu_api.data_timezone)


def test_apply_ccs2_state_eu_default_calls_parser(eu_api, token, ccs2_vehicle):
    """Base _apply_ccs2_state delegates to _update_vehicle_properties_ccs2."""
    from unittest.mock import patch

    state = {"Date": "20260101120010", "Drivetrain": {}}
    with patch.object(eu_api, "_update_vehicle_properties_ccs2") as mock_parser:
        eu_api._apply_ccs2_state(ccs2_vehicle, state)
    mock_parser.assert_called_once_with(ccs2_vehicle, state)
