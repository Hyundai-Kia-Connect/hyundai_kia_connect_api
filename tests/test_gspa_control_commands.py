"""HyundaiCciApiEU GSPA control commands — path/body/auth per endpoint."""

import datetime as dt
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from hyundai_kia_connect_api.ApiImpl import (
    ClimateRequestOptions,
    ScheduleChargingClimateRequestOptions,
    WindowRequestOptions,
)
from hyundai_kia_connect_api.const import (
    CHARGE_PORT_ACTION,
    ORDER_STATUS,
    VALET_MODE_ACTION,
    VEHICLE_LOCK_ACTION,
    WINDOW_STATE,
)
from hyundai_kia_connect_api.exceptions import (
    APIError,
    AuthenticationError,
    UnsupportedControlError,
)
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
    assert "/gspa/v1/valet/vehicles/test123/control" in activate.args[0]
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
        temp_unit=0,
        hvac_temp_type=1,
        driver_seat_location="L",
        duration=10,
        steering_wheel=True,
        side_rear_mirror_heating=True,
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
    assert body["tempUnit"] == 0
    assert body["hvacTempType"] == 1
    assert body["drvSeatLoc"] == "L"
    assert body["ignitionDuration"] == 10
    assert body["strgWhlHeating"] is True
    assert body["sideRearMirrorHeating"] is True
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


def test_windows_all_close_scope_command():
    options = WindowRequestOptions(
        driver_seat_window=WINDOW_STATE.CLOSED,
        passenger_seat_window=WINDOW_STATE.CLOSED,
        rear_left_window=WINDOW_STATE.CLOSED,
        rear_right_window=WINDOW_STATE.CLOSED,
    )
    _, call = _run_command(HyundaiCciApiEU.set_windows_state, options)
    assert call.args[0].endswith("/gspa/v1/remote/vehicles/test123/window-curtain")
    body: dict[str, Any] = call.kwargs["json"]
    assert body["command"] == "window-close"
    assert body["rlSeatWindow"] == WINDOW_STATE.CLOSED.value


def test_windows_front_open_scope():
    options = WindowRequestOptions(
        driver_seat_window=WINDOW_STATE.OPEN,
        passenger_seat_window=WINDOW_STATE.OPEN,
    )
    _, call = _run_command(HyundaiCciApiEU.set_windows_state, options)
    assert call.kwargs["json"]["command"] == "front-open"


def test_windows_mixed_state_raises():
    options = WindowRequestOptions(
        driver_seat_window=WINDOW_STATE.OPEN,
        passenger_seat_window=WINDOW_STATE.CLOSED,
    )
    api = _make_api()
    with pytest.raises(UnsupportedControlError):
        api.set_windows_state(_make_token(), _make_vehicle(), options)


def test_window_curtain_per_seat():
    options = WindowRequestOptions(
        rear_left_curtain=WINDOW_STATE.OPEN,
        rear_right_curtain=WINDOW_STATE.CLOSED,
        driver_seat_location="L",
    )
    _, call = _run_command(HyundaiCciApiEU.set_window_curtain, options)
    body: dict[str, Any] = call.kwargs["json"]
    assert call.args[0].endswith("/window-curtain")
    assert body["command"] == "open"
    assert body["rlSeatWindowCurtain"] == WINDOW_STATE.OPEN.value
    assert body["rrSeatWindowCurtain"] == WINDOW_STATE.CLOSED.value
    assert body["drvSeatLoc"] == "L"


def test_set_charge_limits_bearer_auth():
    _, call = _run_command(HyundaiCciApiEU.set_charge_limits, 80, 60)
    body = call.kwargs["json"]
    assert call.args[0].endswith("/charge-target")
    assert body["targetSOClist"] == [
        {"plugType": 0, "targetSOClevel": 60},
        {"plugType": 1, "targetSOClevel": 80},
    ]
    assert body["command"] == "set"
    # bearer endpoint: no AuthorizationCCSP, standard ccs token auth
    assert "AuthorizationCCSP" not in call.kwargs["headers"]
    assert call.kwargs["headers"]["Authorization"] == "Bearer ccs-token"


def test_set_charging_current():
    _, call = _run_command(HyundaiCciApiEU.set_charging_current, 2)
    assert call.args[0].endswith("/charging-current")
    assert call.kwargs["json"] == {"chargingCurrent": 2, "command": "set"}
    assert "AuthorizationCCSP" not in call.kwargs["headers"]
    assert call.kwargs["headers"]["Authorization"] == "Bearer ccs-token"


def test_set_v2l_discharge_limit():
    _, call = _run_command(HyundaiCciApiEU.set_vehicle_to_load_discharge_limit, 50)
    assert call.args[0].endswith("/discharge-limit")
    assert call.kwargs["json"] == {"dischargingLimit": 50, "command": "set"}
    assert "AuthorizationCCSP" not in call.kwargs["headers"]
    assert call.kwargs["headers"]["Authorization"] == "Bearer ccs-token"


def test_set_charge_alarm_enabled_and_disabled():
    _, on = _run_command(HyundaiCciApiEU.set_charge_alarm, True)
    assert on.kwargs["json"] == {
        "alarmOff": 0,
        "alarmBefore10": 1,
        "alarmBefore20": 1,
        "alarmBefore30": 1,
        "command": "set",
    }
    assert "AuthorizationCCSP" not in on.kwargs["headers"]
    assert on.kwargs["headers"]["Authorization"] == "Bearer ccs-token"
    _, off = _run_command(HyundaiCciApiEU.set_charge_alarm, False)
    assert off.kwargs["json"]["alarmOff"] == 1
    assert off.kwargs["json"]["alarmBefore10"] == 0
    assert "AuthorizationCCSP" not in off.kwargs["headers"]
    assert off.kwargs["headers"]["Authorization"] == "Bearer ccs-token"


def test_schedule_reservation_charge_body():
    options = ScheduleChargingClimateRequestOptions()
    options.charging_enabled = True
    options.off_peak_charge_only_enabled = False
    options.off_peak_start_time = dt.time(1, 30)
    options.off_peak_end_time = dt.time(6, 0)
    _, call = _run_command(HyundaiCciApiEU.schedule_reservation_charge, options)
    assert call.args[0].endswith("/reservation-charge")
    body = call.kwargs["json"]
    assert body["reservFlag"] == 1
    assert body["offpeakPowerFlag"] == 1
    assert body["reservStartTime"] == {"time": "0130", "timeSection": 0}
    assert body["reservEndTime"] == {"time": "0600", "timeSection": 0}
    assert body["command"] == "set"
    assert "AuthorizationCCSP" not in call.kwargs["headers"]
    assert call.kwargs["headers"]["Authorization"] == "Bearer ccs-token"


def test_schedule_reservation_hvac_body_shape():
    options = ScheduleChargingClimateRequestOptions()
    options.first_departure = ScheduleChargingClimateRequestOptions.DepartureOptions()
    options.first_departure.enabled = True
    options.first_departure.days = [1]
    options.first_departure.time = dt.time(7, 5)
    _, call = _run_command(HyundaiCciApiEU.schedule_reservation_hvac, options)
    assert call.args[0].endswith("/reservation-hvac")
    body = call.kwargs["json"]
    assert body["command"] == "set"
    assert body["reservedHVACInfo1"]["reservHVACflag"] == 1
    assert body["reservedHVACInfo2"]["reservHVACflag"] == 0
    assert "AuthorizationCCSP" not in call.kwargs["headers"]
    assert call.kwargs["headers"]["Authorization"] == "Bearer ccs-token"


def test_schedule_reservation_engine_body_shape():
    options = ScheduleChargingClimateRequestOptions()
    options.first_departure = ScheduleChargingClimateRequestOptions.DepartureOptions()
    options.first_departure.enabled = True
    options.first_departure.days = [1]
    options.first_departure.time = dt.time(7, 5)
    _, call = _run_command(HyundaiCciApiEU.schedule_reservation_engine, options)
    assert call.args[0].endswith("/reservation-engine")
    body = call.kwargs["json"]
    assert body["reservInfo"]["scheduleEnable"] is True
    assert body["reservInfo"]["day"] == [1]
    assert body["reservInfo2"]["scheduleEnable"] is False
    assert "AuthorizationCCSP" not in call.kwargs["headers"]
    assert call.kwargs["headers"]["Authorization"] == "Bearer ccs-token"


def test_schedule_charging_and_climate_body_shape():
    options = ScheduleChargingClimateRequestOptions()
    options.charging_enabled = True
    options.climate_enabled = False
    options.temperature = 21.5
    options.temperature_unit = 0
    options.defrost = False
    options.first_departure = ScheduleChargingClimateRequestOptions.DepartureOptions()
    options.first_departure.enabled = True
    options.first_departure.days = [1]
    options.first_departure.time = dt.time(7, 5)
    options.second_departure = ScheduleChargingClimateRequestOptions.DepartureOptions()
    options.off_peak_start_time = dt.time(1, 0)
    options.off_peak_end_time = dt.time(5, 0)
    options.off_peak_charge_only_enabled = False
    _, call = _run_command(HyundaiCciApiEU.schedule_charging_and_climate, options)
    assert call.args[0].endswith("/reservation-charge-hvac")
    body = call.kwargs["json"]
    assert body["reservFlag"] == 1
    assert body["command"] == "set"
    info1 = body["reservChargeInfo"]["reservChargeInfo1"]
    assert info1["reservChargeSet"] is True
    assert info1["reservInfo"]["day"] == [1]
    assert info1["reservInfo"]["time"] == {"time": "0705", "timeSection": 0}
    assert info1["reservFatcSet"]["airTemp"]["value"] == "21.5"
    assert "offPeakPowerInfo" in body
    assert "AuthorizationCCSP" not in call.kwargs["headers"]
    assert call.kwargs["headers"]["Authorization"] == "Bearer ccs-token"


def test_lock_and_start_toggle():
    _, call = _run_command(HyundaiCciApiEU.lock_and_start_toggle, True)
    assert call.args[0].endswith("/lock-and-start-toggle")
    assert call.kwargs["json"] == {"lockAndStartEnable": True}
    assert "AuthorizationCCSP" not in call.kwargs["headers"]
    assert call.kwargs["headers"]["Authorization"] == "Bearer ccs-token"


def test_control_command_rc_not_0000_raises_api_error():
    api = _make_api()
    token = _make_token()
    vehicle = _make_vehicle()
    with (
        patch("hyundai_kia_connect_api.GspaApiEU.requests.post") as post,
        patch.object(HyundaiCciApiEU, "_get_control_token") as get_ct,
    ):
        get_ct.return_value = ("Bearer ctrl-token-abc", 4_000_000_000)
        post.return_value = MagicMock(
            status_code=200,
            json=lambda: {"rc": "500-001", "msg": "internal error"},
        )
        with pytest.raises(APIError) as exc_info:
            HyundaiCciApiEU.lock_action(api, token, vehicle, VEHICLE_LOCK_ACTION.LOCK)
    assert "500-001" in str(exc_info.value)
    assert "internal error" in str(exc_info.value)
    # only one POST — rc != "0000" on a 200 must not retry
    assert post.call_count == 1


def test_control_command_retry_exhausted_raises_auth_error():
    api = _make_api()
    token = _make_token()
    vehicle = _make_vehicle()
    err = MagicMock(status_code=401, json=dict)
    with (
        patch("hyundai_kia_connect_api.GspaApiEU.requests.post") as post,
        patch.object(HyundaiCciApiEU, "_get_control_token") as get_ct,
    ):
        get_ct.return_value = ("Bearer ctrl-token-abc", 4_000_000_000)
        post.side_effect = [err, err]
        with pytest.raises(AuthenticationError):
            HyundaiCciApiEU.lock_action(api, token, vehicle, VEHICLE_LOCK_ACTION.LOCK)
    assert post.call_count == 2


def test_control_command_spring_404_raises_unsupported():
    """Spring "No static resource" body classifies as UnsupportedControlError."""
    api = _make_api()
    token = _make_token()
    vehicle = _make_vehicle()
    with (
        patch("hyundai_kia_connect_api.GspaApiEU.requests.post") as post,
        patch.object(HyundaiCciApiEU, "_get_control_token") as get_ct,
    ):
        get_ct.return_value = ("Bearer ctrl-token-abc", 4_000_000_000)
        post.return_value = MagicMock(
            status_code=404,
            json=lambda: {
                "timestamp": "2026-09-01T00:00:00.000+00:00",
                "status": 404,
                "error": "Not Found",
                "message": ("No static resource gspa/v1/remote/vehicles/test123/door."),
                "path": "/gspa/v1/remote/vehicles/test123/door",
            },
        )
        with pytest.raises(UnsupportedControlError) as exc_info:
            HyundaiCciApiEU.lock_action(api, token, vehicle, VEHICLE_LOCK_ACTION.LOCK)
    assert "No static resource" in str(exc_info.value)


def test_control_command_success_standardized_envelope():
    """Live-probed 2026-09-05: a successful command returns the
    standardized {"data", "metaInfo"} envelope (HTTP 202); the polling SID
    sits in "data" (CarRemoteControlApiResponse) and metaInfo.retCode
    gates success."""
    api = _make_api()
    token = _make_token()
    vehicle = _make_vehicle()
    body = {
        "data": {"SID": "sid-std", "svcSID": "svc-std", "svcTime": "0"},
        "metaInfo": {"retCode": "S", "resCode": "202-000", "msgId": "x"},
    }
    with (
        patch("hyundai_kia_connect_api.GspaApiEU.requests.post") as post,
        patch.object(HyundaiCciApiEU, "_get_control_token") as get_ct,
    ):
        get_ct.return_value = ("Bearer ctrl-token-abc", 4_000_000_000)
        post.return_value = MagicMock(status_code=202, json=lambda: body)
        action_id = api.stop_rear_seat_alarm(token, vehicle)
    assert action_id == "gspa:sid-std"


def test_control_command_svc_sid_only_standardized_envelope():
    """Some commands return only svcSID in the standardized envelope."""
    api = _make_api()
    token = _make_token()
    vehicle = _make_vehicle()
    body = {
        "data": {"svcSID": "svc-only", "svcTime": "0"},
        "metaInfo": {"retCode": "S", "resCode": "202-000"},
    }
    with (
        patch("hyundai_kia_connect_api.GspaApiEU.requests.post") as post,
        patch.object(HyundaiCciApiEU, "_get_control_token") as get_ct,
    ):
        get_ct.return_value = ("Bearer ctrl-token-abc", 4_000_000_000)
        post.return_value = MagicMock(status_code=202, json=lambda: body)
        action_id = api.stop_rear_seat_alarm(token, vehicle)
    assert action_id == "gspa:svc-only"


def test_control_command_2xx_business_error_raises():
    """A 2xx response with metaInfo.retCode "F" is a business error and
    raises a typed exception instead of parsing as a success (live-observed
    shape: retCode "F" + resCode "403-006")."""
    api = _make_api()
    token = _make_token()
    vehicle = _make_vehicle()
    body = {
        "data": {},
        "metaInfo": {"retCode": "F", "resCode": "403-006", "msgId": "x"},
    }
    with (
        patch("hyundai_kia_connect_api.GspaApiEU.requests.post") as post,
        patch.object(HyundaiCciApiEU, "_get_control_token") as get_ct,
    ):
        get_ct.return_value = ("Bearer ctrl-token-abc", 4_000_000_000)
        post.return_value = MagicMock(status_code=200, json=lambda: body)
        with pytest.raises(AuthenticationError):
            api.stop_rear_seat_alarm(token, vehicle)
