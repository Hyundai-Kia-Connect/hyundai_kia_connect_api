"""HyundaiCciApiEU GSPA control commands — path/body/auth per endpoint."""

import datetime as dt
from unittest.mock import MagicMock, patch

import pytest

from hyundai_kia_connect_api.ApiImpl import ClimateRequestOptions
from hyundai_kia_connect_api.const import (
    CHARGE_PORT_ACTION,
    ORDER_STATUS,
    VALET_MODE_ACTION,
    VEHICLE_LOCK_ACTION,
)
from hyundai_kia_connect_api.exceptions import UnsupportedControlError
from hyundai_kia_connect_api.HyundaiCciApiEU import HyundaiCciApiEU
from hyundai_kia_connect_api.Token import Token
from hyundai_kia_connect_api.Vehicle import Vehicle

ENVELOPE = {"rc": "0000", "rs": {"SID": "sid-1"}, "msg": "success"}


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


def _run_command(meth, *args, **kwargs):
    """Run a public control method with mocked transport+PIN; return the POST call."""
    api = _make_api()
    token = _make_token()
    vehicle = _make_vehicle()
    with (
        patch("hyundai_kia_connect_api.GspaApiEU.requests.post") as post,
        patch.object(HyundaiCciApiEU, "_get_control_token") as get_ct,
    ):
        get_ct.return_value = ("Bearer ctrl-token-abc", 4_000_000_000)
        post.return_value = MagicMock(status_code=200, json=lambda: ENVELOPE)
        action_id = meth(api, token, vehicle, *args, **kwargs)
    return action_id, post.call_args


def test_lock_action_unlock():
    action_id, call = _run_command(
        HyundaiCciApiEU.lock_action, VEHICLE_LOCK_ACTION.UNLOCK
    )
    assert action_id == "gspa:sid-1"
    assert call.args[0].endswith("/gspa/v1/remote/vehicles/test123/door")
    assert call.kwargs["json"] == {"command": "open"}
    assert call.kwargs["headers"]["AuthorizationCCSP"] == "Bearer ctrl-token-abc"


def test_lock_action_lock():
    _, call = _run_command(HyundaiCciApiEU.lock_action, VEHICLE_LOCK_ACTION.LOCK)
    assert call.kwargs["json"] == {"command": "close"}


def test_door_power_off():
    _, call = _run_command(HyundaiCciApiEU.door_power_off)
    assert call.args[0].endswith("/gspa/v1/remote/vehicles/test123/door-power-off")
    assert call.kwargs["json"] == {"command": "CLOSE"}


def test_start_stop_charge():
    _, start = _run_command(HyundaiCciApiEU.start_charge)
    _, stop = _run_command(HyundaiCciApiEU.stop_charge)
    assert start.args[0].endswith("/gspa/v1/remote/vehicles/test123/charge")
    assert start.kwargs["json"] == {"command": "start"}
    assert stop.kwargs["json"] == {"command": "stop"}


def test_charge_port_action():
    _, call = _run_command(HyundaiCciApiEU.charge_port_action, CHARGE_PORT_ACTION.OPEN)
    assert call.args[0].endswith("/gspa/v1/remote/vehicles/test123/portdoor")
    assert call.kwargs["json"] == {"command": "open"}


def test_open_frunk():
    _, call = _run_command(HyundaiCciApiEU.open_frunk)
    assert call.args[0].endswith("/gspa/v1/remote/vehicles/test123/frunk")
    assert call.kwargs["json"] == {"command": "open"}


def test_hazard_and_hornlight_and_lamp():
    _, light = _run_command(HyundaiCciApiEU.start_hazard_lights)
    _, horn = _run_command(HyundaiCciApiEU.start_hazard_lights_and_horn)
    _, lamp = _run_command(HyundaiCciApiEU.turn_off_lamp)
    assert light.args[0].endswith("/light") and light.kwargs["json"] == {
        "command": "on"
    }
    assert horn.args[0].endswith("/horn-light") and horn.kwargs["json"] == {
        "command": "on"
    }
    assert lamp.args[0].endswith("/lamp") and lamp.kwargs["json"] == {
        "command": "all-off"
    }


def test_battery_conditioning():
    _, start = _run_command(HyundaiCciApiEU.start_battery_conditioning)
    _, stop = _run_command(HyundaiCciApiEU.stop_battery_conditioning)
    assert start.args[0].endswith("/battery-conditioning")
    assert start.kwargs["json"] == {"command": "start"}
    assert stop.kwargs["json"] == {"command": "stop"}


def test_stop_rear_seat_alarm_uses_safety_prefix():
    _, call = _run_command(HyundaiCciApiEU.stop_rear_seat_alarm)
    assert "/gspa/v1/safety/vehicles/test123/rearseat-alarm" in call.args[0]
    assert call.kwargs["json"] == {"command": "stop"}


def test_valet_mode_action_uses_valet_prefix():
    _, activate = _run_command(
        HyundaiCciApiEU.valet_mode_action, VALET_MODE_ACTION.ACTIVATE
    )
    assert "/gspa/v1/valet/vehicles/test123/valet" in activate.args[0]
    assert activate.kwargs["json"] == {"command": "activate"}
    _, deactivate = _run_command(
        HyundaiCciApiEU.valet_mode_action, VALET_MODE_ACTION.DEACTIVATE
    )
    assert deactivate.kwargs["json"] == {"command": "deactivate"}


def test_check_action_status_dispatch():
    api = _make_api()
    with patch("hyundai_kia_connect_api.GspaApiEU.requests.get") as get:
        get.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                "metaInfo": {"retCode": "S"},
                "data": {"pollingState": "SUCCESS"},
            },
        )
        status = api.check_action_status(_make_token(), _make_vehicle(), "gspa:sid-1")
    assert status is ORDER_STATUS.SUCCESS


def test_check_action_status_rejects_foreign_prefixes():
    api = _make_api()
    for bad in ("ccapi:xyz", "ccsp:xyz", "unprefixed-id"):
        with pytest.raises(UnsupportedControlError):
            api.check_action_status(_make_token(), _make_vehicle(), bad)


def test_control_command_pre_ccs2_raises_unsupported():
    api = _make_api()
    with pytest.raises(UnsupportedControlError):
        api._control_command(
            _make_token(), _make_vehicle(ccs2=0), "door", {"command": "close"}
        )


def test_start_climate_full_options():
    options = ClimateRequestOptions(
        set_temp=21.5,
        defrost=True,
        heating=1,
        duration=10,
        steering_wheel=True,
        front_left_seat=1,
        front_right_seat=0,
        rear_left_seat=2,
    )
    _, call = _run_command(HyundaiCciApiEU.start_climate, options)
    assert call.args[0].endswith("/gspa/v1/remote/vehicles/test123/temperature")
    body = call.kwargs["json"]
    assert body["command"] == "start"
    assert body["hvacTemp"] == "21.5"
    assert body["windshieldFrontDefogState"] is True
    assert body["heating1"] == 1
    assert body["ignitionDuration"] == 10
    assert body["strgWhlHeating"] is True
    assert body["seatClimateInfo"] == {
        "drvSeatClimateState": 1,
        "psgSeatClimateState": 0,
        "rlSeatClimateState": 2,
    }
    assert call.kwargs["headers"]["AuthorizationCCSP"] == "Bearer ctrl-token-abc"


def test_start_climate_minimal():
    _, call = _run_command(HyundaiCciApiEU.start_climate, ClimateRequestOptions())
    body = call.kwargs["json"]
    assert body == {"command": "start"}


def test_stop_climate():
    _, call = _run_command(HyundaiCciApiEU.stop_climate)
    assert call.args[0].endswith("/temperature")
    assert call.kwargs["json"] == {"command": "stop"}


def test_start_engine_maps_climate_field():
    options = ClimateRequestOptions(set_temp=22.0, climate=True)
    _, call = _run_command(HyundaiCciApiEU.start_engine, options)
    body = call.kwargs["json"]
    assert call.args[0].endswith("/engine")
    assert body["command"] == "start"
    assert body["hvacTemp"] == "22.0"
    assert body["hvacCtrl"] == 1


def test_stop_engine():
    _, call = _run_command(HyundaiCciApiEU.stop_engine)
    assert call.kwargs["json"] == {"command": "stop"}


def test_pet_care_start_stop():
    _, start = _run_command(HyundaiCciApiEU.start_pet_care)
    assert start.kwargs["json"] == {"hvacTemp": "21", "tempUnit": "F"}
    _, stop = _run_command(HyundaiCciApiEU.stop_pet_care)
    assert stop.kwargs["json"] == {"hvacTemp": "21", "tempUnit": "C"}


def test_pet_care_start_with_temp():
    _, call = _run_command(
        HyundaiCciApiEU.start_pet_care, ClimateRequestOptions(set_temp=23.0)
    )
    assert call.kwargs["json"] == {"hvacTemp": "23.0", "tempUnit": "F"}
