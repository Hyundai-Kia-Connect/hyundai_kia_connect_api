"""IN CCS2 force_refresh: resMsg root + state.time (IN-specific shape)."""

import datetime as dt
from unittest.mock import MagicMock, patch

import pytest

from hyundai_kia_connect_api.KiaUvoApiIN import KiaUvoApiIN
from hyundai_kia_connect_api.Token import Token
from hyundai_kia_connect_api.Vehicle import Vehicle
from hyundai_kia_connect_api.const import DISTANCE_UNITS, ENGINE_TYPES


@pytest.fixture
def in_api():
    return KiaUvoApiIN(brand=2)


@pytest.fixture
def ccs2_vehicle():
    v = Vehicle()
    v.id = "test-vid"
    v.engine_type = ENGINE_TYPES.EV
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


def test_in_inspect_ccs2_response_resmsg_root(in_api):
    """IN _inspect reads resMsg root + state.time (not state.Vehicle)."""
    response = {"retCode": "S", "resMsg": {"time": "20260101120010", "engine": False}}
    state, last_updated = in_api._inspect_ccs2_response(response)
    assert state == {"time": "20260101120010", "engine": False}
    assert last_updated is not None
    assert last_updated.tzinfo is not None


def test_in_force_refresh_applies_via_in_parser(in_api, token, ccs2_vehicle):
    """IN force_refresh uses IN _update_vehicle_properties (not CCS2 parser)."""
    # NOTE: IN get_last_updated_at parses the string treating wall-clock fields
    # as data_timezone (India, UTC+5:30), so .timestamp() is offset by -5:30 vs
    # UTC. Use +6h to keep last_updated.timestamp() > trigger_time despite the
    # tz interpretation. See task-6 report for details.
    fresh_time = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=6)).strftime(
        "%Y%m%d%H%M%S"
    )

    def mock_get(url, **kwargs):
        resp = MagicMock()
        if url.endswith("/ccs2/carstatus") and not url.endswith("/latest"):
            resp.json.return_value = {"retCode": "S", "resCode": "0000"}
        else:
            resp.json.return_value = {
                "retCode": "S",
                "resMsg": {"time": fresh_time, "engine": False},
            }
        return resp

    with (
        patch.object(in_api.session, "get", side_effect=mock_get),
        patch("hyundai_kia_connect_api.ApiImplType1.sleep"),
        patch.object(in_api, "_update_vehicle_properties") as mock_in_parser,
        patch.object(in_api, "_update_vehicle_location"),
    ):
        in_api._force_refresh_vehicle_state_ccs2(token, ccs2_vehicle)

    mock_in_parser.assert_called_once()
