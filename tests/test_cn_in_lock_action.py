"""CN and IN use the inherited ApiImplType1.lock_action.

Legacy vehicles post {"action", "deviceId"} to v1 control/door with the
access token; CCS2 vehicles post {"command"} to v2 ccs2/control/door with
the PIN-derived control token.
"""

from unittest.mock import MagicMock

import pytest

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
