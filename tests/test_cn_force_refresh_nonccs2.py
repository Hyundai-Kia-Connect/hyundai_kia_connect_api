"""CN force_refresh: non-CCS2 path + CCS2 path via base hooks (resMsg root)."""

import datetime as dt
from unittest.mock import MagicMock, patch

import pytest

from hyundai_kia_connect_api.KiaUvoApiCN import KiaUvoApiCN
from hyundai_kia_connect_api.Token import Token
from hyundai_kia_connect_api.Vehicle import Vehicle
from hyundai_kia_connect_api.const import ENGINE_TYPES


@pytest.fixture
def cn_api():
    return KiaUvoApiCN(region=4, brand=2, language="en")


@pytest.fixture
def nonccs2_vehicle():
    v = Vehicle()
    v.id = "test-vid"
    v.engine_type = ENGINE_TYPES.PHEV
    # NOTE: real CN vehicles never populate ccu_ccs2_protocol_support, so the
    # flag stays at its default None (not 0). Base is_ccs2 check is `flag != 0`
    # -> None != 0 is True -> CCS2 path IS invoked, and the CN hooks
    # (_inspect_ccs2_response / _apply_ccs2_state) handle the resMsg-root shape.
    # This fixture pins flag=0 to characterize the genuine non-CCS2 sub-case.
    v.ccu_ccs2_protocol_support = 0
    return v


@pytest.fixture
def ccs2_vehicle():
    """Real-world CN default: ccu_ccs2_protocol_support is None (not 0).

    `None != 0` is True -> CCS2 path invoked. CN hooks parse resMsg root.
    """
    v = Vehicle()
    v.id = "test-vid"
    v.engine_type = ENGINE_TYPES.PHEV
    v.ccu_ccs2_protocol_support = None
    return v


@pytest.fixture
def token():
    t = Token()
    t.access_token = "Bearer test"
    t.pin = "1234"
    t.device_id = "dev"
    return t


def test_cn_nonccs2_force_refresh_does_not_call_ccs2(cn_api, token, nonccs2_vehicle):
    """CN force_refresh on non-CCS2 vehicle uses _get_forced_vehicle_state, not ccs2."""
    with (
        patch.object(cn_api, "_force_refresh_vehicle_state_ccs2") as mock_ccs2,
        patch.object(cn_api, "_get_forced_vehicle_state", return_value={}),
        patch.object(cn_api, "_get_location", return_value=None),
        patch.object(cn_api, "_update_vehicle_properties"),
        patch.object(cn_api, "_get_driving_info", side_effect=Exception),
    ):
        cn_api.force_refresh_vehicle_state(token, nonccs2_vehicle)
    mock_ccs2.assert_not_called()


def test_cn_inspect_ccs2_response_resmsg_root(cn_api):
    """CN _inspect reads resMsg root + status.time (not state.Vehicle)."""
    response = {
        "retCode": "S",
        "resMsg": {"status": {"time": "20260101120010", "engine": False}},
    }
    state, last_updated = cn_api._inspect_ccs2_response(response)
    assert state == {"status": {"time": "20260101120010", "engine": False}}
    assert last_updated is not None
    assert last_updated.tzinfo is not None


def test_cn_force_refresh_uses_cn_hooks(cn_api, token, ccs2_vehicle):
    """CN CCS2 force_refresh uses base polling loop + CN hooks (not CCS2 parser).

    Reproduces the real-world CN scenario: ccu_ccs2_protocol_support=None ->
    CCS2 path invoked. The base _force_refresh_vehicle_state_ccs2 polls
    /ccs2/carstatus/latest and calls _inspect_ccs2_response (CN hook) until
    fresh, then _apply_ccs2_state (CN hook -> _update_vehicle_properties,
    the non-CCS2 parser). Location is refreshed via the base default
    _post_refresh_ccs2_location (CN _get_location returns resMsg, which
    matches the base default coord.lat/lon/time unpacking).
    """
    # NOTE: CN parse_datetime(status.time, data_timezone) treats the string as
    # Asia/Shanghai wall-clock (UTC+8). If the string represents UTC wall-clock,
    # the resulting epoch is off by -8h vs UTC. Use +9h to keep
    # last_updated.timestamp() > trigger_time despite the tz interpretation.
    # Production risk: if the CN server returns status.time in UTC, every
    # force-refresh will timeout (deferred to live dump).
    fresh_time = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=9)).strftime(
        "%Y%m%d%H%M%S"
    )

    def mock_get(url, **kwargs):
        resp = MagicMock()
        if url.endswith("/ccs2/carstatus") and not url.endswith("/latest"):
            resp.json.return_value = {"retCode": "S", "resCode": "0000"}
        else:
            resp.json.return_value = {
                "retCode": "S",
                "resMsg": {"status": {"time": fresh_time, "engine": False}},
            }
        return resp

    with (
        patch.object(cn_api.session, "get", side_effect=mock_get),
        patch("hyundai_kia_connect_api.ApiImplType1.sleep"),
        patch.object(cn_api, "_update_vehicle_properties") as mock_cn_parser,
        patch.object(cn_api, "_update_vehicle_properties_ccs2") as mock_ccs2_parser,
        patch.object(cn_api, "_get_location", return_value=None),
    ):
        cn_api._force_refresh_vehicle_state_ccs2(token, ccs2_vehicle)

    mock_cn_parser.assert_called_once()
    mock_ccs2_parser.assert_not_called()
