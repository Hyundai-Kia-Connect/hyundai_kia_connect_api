"""CN and IN send CCS2 vehicles through the ApiImplType1 control paths.

lock_action is inherited outright. start_climate / stop_climate keep the
region's own legacy request and defer to the parent for CCS2 vehicles,
which use v2 ccs2/control/* with the PIN-derived control token.
"""

from unittest.mock import MagicMock

import pytest

from hyundai_kia_connect_api.ApiImpl import ClimateRequestOptions
from hyundai_kia_connect_api.const import VEHICLE_LOCK_ACTION
from hyundai_kia_connect_api.KiaUvoApiCN import KiaUvoApiCN
from hyundai_kia_connect_api.KiaUvoApiIN import KiaUvoApiIN
from hyundai_kia_connect_api.Token import Token
from hyundai_kia_connect_api.Vehicle import Vehicle


@pytest.fixture(
    params=[
        lambda: KiaUvoApiCN(region=4, brand=1, language="en"),
        lambda: KiaUvoApiIN(brand=2),
    ],
    ids=["CN", "IN"],
)
def api(request):
    api = request.param()
    api.session = MagicMock()
    api.session.post.return_value.json.return_value = {
        "retCode": "S",
        "resCode": "0000",
        "msgId": "msg-1",
    }
    api._get_control_token = MagicMock(return_value=("Bearer ctl", 0.0))
    return api


def _token() -> Token:
    return Token(access_token="Bearer acc", device_id="dev-1", pin="1234")


def test_legacy_vehicle_uses_v1_door(api):
    vehicle = Vehicle(id="veh-1", ccu_ccs2_protocol_support=0)

    assert api.lock_action(_token(), vehicle, VEHICLE_LOCK_ACTION.LOCK) == "msg-1"

    url = api.session.post.call_args.args[0]
    kwargs = api.session.post.call_args.kwargs
    assert url == api.SPA_API_URL + "vehicles/veh-1/control/door"
    assert kwargs["json"] == {"action": "close", "deviceId": "dev-1"}
    assert kwargs["headers"]["Authorization"] == "Bearer acc"


def test_ccs2_vehicle_uses_v2_ccs2_door(api):
    vehicle = Vehicle(id="veh-1", ccu_ccs2_protocol_support=1)

    assert api.lock_action(_token(), vehicle, VEHICLE_LOCK_ACTION.UNLOCK) == "msg-1"

    url = api.session.post.call_args.args[0]
    kwargs = api.session.post.call_args.kwargs
    assert url == api.SPA_API_URL_V2 + "vehicles/veh-1/ccs2/control/door"
    assert kwargs["json"] == {"command": "open"}
    assert kwargs["headers"]["Authorization"] == "Bearer ctl"


def _post(api):
    call = api.session.post.call_args
    return call.args[0], call.kwargs


def test_legacy_start_climate_keeps_region_request(api):
    vehicle = Vehicle(id="veh-1", ccu_ccs2_protocol_support=0)

    api.start_climate(_token(), vehicle, ClimateRequestOptions())

    url, kwargs = _post(api)
    assert url == api.SPA_API_URL + "vehicles/veh-1/control/engine"
    assert kwargs["json"]["action"] == "start"
    assert kwargs["json"]["hvacType"] == 1


def test_ccs2_start_climate_uses_ccs2_temperature(api):
    vehicle = Vehicle(id="veh-1", ccu_ccs2_protocol_support=1)

    assert api.start_climate(_token(), vehicle, ClimateRequestOptions()) == "msg-1"

    url, kwargs = _post(api)
    assert url == api.SPA_API_URL_V2 + "vehicles/veh-1/ccs2/control/temperature"
    assert kwargs["json"]["command"] == "start"
    assert kwargs["json"]["hvacTemp"] == 21
    assert kwargs["headers"]["Authorization"] == "Bearer ctl"


def test_legacy_stop_climate_keeps_region_request(api):
    vehicle = Vehicle(id="veh-1", ccu_ccs2_protocol_support=0)

    api.stop_climate(_token(), vehicle)

    url, kwargs = _post(api)
    assert url == api.SPA_API_URL_V2 + "vehicles/veh-1/control/engine"
    assert kwargs["json"] == {"action": "stop"}


def test_ccs2_stop_climate_uses_ccs2_temperature(api):
    vehicle = Vehicle(id="veh-1", ccu_ccs2_protocol_support=1)

    assert api.stop_climate(_token(), vehicle) == "msg-1"

    url, kwargs = _post(api)
    assert url == api.SPA_API_URL_V2 + "vehicles/veh-1/ccs2/control/temperature"
    assert kwargs["json"] == {"command": "stop"}
    assert kwargs["headers"]["Authorization"] == "Bearer ctl"
