"""Offline Japan command envelopes and truthful completion semantics.

Wire literals were verified against raw constructor instructions in Japan app
1.0.17 RemoteRepository$Command, independently of Java enum reconstruction.
"""

import copy
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from hyundai_kia_connect_api.ApiImpl import (
    ClimateRequestOptions,
    POICoord,
    POIInfo,
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
    AuthenticationError,
    InvalidAPIResponseError,
    UnsupportedControlError,
)
from hyundai_kia_connect_api.japan_commands import JapanCommandsMixin
from hyundai_kia_connect_api.japan_mqtt import JapanMqttSession


class Adapter(JapanCommandsMixin):
    def __init__(self):
        self._request = Mock(
            side_effect=[
                {"controlToken": "synthetic-control", "expiresTime": 60},
                {"msgId": "synthetic-action"},
            ]
        )
        self.helper = Mock()
        self.helper.request_context = {}
        self.helper.status.return_value = ORDER_STATUS.PENDING
        self._mqtt_for_vehicle = Mock(return_value=self.helper)
        self._vehicle_profiles = {"synthetic-car": {"option": {"hvacTempType": 1}}}
        self._charge_target_cache = {"synthetic-car": (0, {0: 100, 1: 100})}

    def _headers(self, token):
        return {"Authorization": "Bearer synthetic-access"}

    def _vehicle_headers(self, token, vehicle, helper=None):
        headers = self._headers(token)
        headers["ccuCCS2ProtocolSupport"] = "1"
        if helper is not None:
            headers.update(helper.request_context)
        return headers

    def _legacy_temperature(self, vehicle, payload):
        return 20.0 if payload["value"] == "0CH" else None


def setup():
    return (
        Adapter(),
        SimpleNamespace(
            pin="synthetic-pin",
            mqtt_registration_uuid="synthetic-uuid",
            device_id="synthetic-device",
            control_token=None,
            control_token_expiry=0,
        ),
        SimpleNamespace(id="synthetic-car"),
    )


@pytest.mark.parametrize(
    "method,args,endpoint,command",
    [
        ("lock_action", (VEHICLE_LOCK_ACTION.LOCK,), "door", "close"),
        ("lock_action", (VEHICLE_LOCK_ACTION.UNLOCK,), "door", "open"),
        ("start_charge", (), "charge", "start"),
        ("stop_charge", (), "charge", "stop"),
        ("stop_climate", (), "temperature", "stop"),
    ],
)
def test_exact_command_payload_and_pin_headers(method, args, endpoint, command):
    api, token, vehicle = setup()
    assert getattr(api, method)(token, vehicle, *args) == "synthetic-action"
    grant, request = api._request.call_args_list
    assert grant.args == ("PUT", "/api/v1/user/pin")
    assert grant.kwargs["json"] == {
        "deviceId": "synthetic-device",
        "pin": "synthetic-pin",
    }
    assert request.args == (
        "POST",
        f"/api/v2/spa/vehicles/synthetic-car/ccs2/control/{endpoint}",
    )
    assert request.kwargs["json"] == {"command": command}
    assert request.kwargs["headers"]["Authorization"] == "Bearer synthetic-control"
    api.helper.prepare.assert_called_once()
    api.helper.track.assert_called_once_with("synthetic-action")
    assert (
        api.check_action_status(token, vehicle, "synthetic-action")
        == ORDER_STATUS.PENDING
    )


def test_command_context_is_attached_after_prepare_not_before():
    api, token, vehicle = setup()

    def prepared():
        api.helper.request_context = {
            "clientId": "synthetic-client",
            "vehicleId": "synthetic-broker-vehicle",
        }

    api.helper.prepare.side_effect = prepared
    api.lock_action(token, vehicle, VEHICLE_LOCK_ACTION.UNLOCK)
    request = api._request.call_args_list[-1]
    assert request.kwargs["headers"]["clientId"] == "synthetic-client"
    assert request.kwargs["headers"]["vehicleId"] == "synthetic-broker-vehicle"
    assert request.kwargs["headers"]["ccuCCS2ProtocolSupport"] == "1"
    assert request.kwargs["headers"]["Authorization"] == "Bearer synthetic-control"
    assert request.kwargs["json"] == {"command": "open"}
    assert "tid" not in request.kwargs["json"]


def test_missing_pin_never_connects_or_submits():
    api, token, vehicle = setup()
    token.pin = None
    with pytest.raises(AuthenticationError):
        api.start_charge(token, vehicle)
    api._request.assert_not_called()
    api._mqtt_for_vehicle.assert_not_called()


@pytest.mark.parametrize("identity", [None, "", "   ", "invalid/identity"])
def test_old_token_mqtt_identity_requires_relogin_before_pin_grant(identity):
    api, token, vehicle = setup()
    token.mqtt_registration_uuid = identity
    with pytest.raises(AuthenticationError):
        api.start_charge(token, vehicle)
    api._request.assert_not_called()
    api._mqtt_for_vehicle.assert_not_called()


def test_whitespace_pin_never_grants_control_token():
    api, token, vehicle = setup()
    token.pin = " \t "
    with pytest.raises(AuthenticationError):
        api.start_charge(token, vehicle)
    api._request.assert_not_called()
    api._mqtt_for_vehicle.assert_not_called()


def test_unknown_or_other_vehicle_request_never_reports_success():
    api, token, vehicle = setup()
    assert api.check_action_status(token, vehicle, "untracked") == ORDER_STATUS.UNKNOWN
    api.start_charge(token, vehicle)
    assert (
        api.check_action_status(
            token, SimpleNamespace(id="other-car"), "synthetic-action"
        )
        == ORDER_STATUS.UNKNOWN
    )
    assert (
        api.check_action_status(token, vehicle, "synthetic-action", True, 500)
        == ORDER_STATUS.PENDING
    )
    api.helper.status.assert_called_with("synthetic-action", timeout=120)


def test_cloud_charge_target_success_does_not_require_control_pin_or_mqtt():
    api, token, vehicle = setup()
    token.pin = None
    api._request.side_effect = [{"retCode": "S"}]
    action_id = api.set_charge_limits(token, vehicle, ac=80, dc=90)
    assert api._request.call_args.kwargs["json"] == {
        "targetSOClist": [
            {"plugType": 0, "targetSOClevel": 90},
            {"plugType": 1, "targetSOClevel": 80},
        ]
    }
    assert api.check_action_status(token, vehicle, action_id) == ORDER_STATUS.SUCCESS
    api._mqtt_for_vehicle.assert_not_called()
    assert vehicle.id not in api._charge_target_cache


def test_charge_target_preserves_alternate_ac_plug_type():
    api, token, vehicle = setup()
    api._charge_target_cache[vehicle.id] = (0, {0: 100, 2: 100})
    api._request.side_effect = [{"retCode": "S"}]
    api.set_charge_limits(token, vehicle, ac=80, dc=90)
    assert api._request.call_args.kwargs["json"]["targetSOClist"][1]["plugType"] == 2


def test_climate_numeric_celsius_envelope_and_unsupported_options():
    api, token, vehicle = setup()
    options = ClimateRequestOptions(set_temp=20, defrost=False)
    api.start_climate(token, vehicle, options)
    assert api._request.call_args.kwargs["json"] == {
        "command": "start",
        "hvacTemp": "20.0",
        "hvacTempType": 1,
        "tempUnit": "C",
        "windshieldFrontDefogState": False,
    }
    api._request.reset_mock()
    options.duration = 10
    with pytest.raises(UnsupportedControlError):
        api.start_climate(token, vehicle, options)
    api._request.assert_not_called()


def test_incomplete_command_ack_never_creates_success_tracking():
    api, token, vehicle = setup()
    api._request.side_effect = [
        {"controlToken": "synthetic-control", "expiresTime": 60},
        {},
    ]
    with pytest.raises(InvalidAPIResponseError):
        api.start_charge(token, vehicle)
    api.helper.track.assert_not_called()
    api.helper.close.assert_called_once()


@pytest.mark.parametrize(
    "status", [ORDER_STATUS.SUCCESS, ORDER_STATUS.FAILED, ORDER_STATUS.TIMEOUT]
)
def test_terminal_status_survives_helper_cleanup(status):
    api, token, vehicle = setup()
    api.start_charge(token, vehicle)
    api.helper.status.return_value = status
    assert api.check_action_status(token, vehicle, "synthetic-action") == status
    api.helper.close.assert_called_once()
    api.helper.status.reset_mock()
    assert api.check_action_status(token, vehicle, "synthetic-action") == status
    api.helper.status.assert_not_called()


def test_default_temperature_and_rhd_seat_capability_mapping():
    api, token, vehicle = setup()
    vehicle.air_temperature = 20.0
    api._vehicle_profiles[vehicle.id]["option"].update(
        {
            "drvSeatLoc": "R",
            "clmtCtrlOption": 1,
            "seatHeaterVent": {"drvSeatHeat": 1, "astSeatHeat": 1},
        }
    )
    options = ClimateRequestOptions(front_left_seat=6, front_right_seat=8, duration=5)
    api.start_climate(token, vehicle, options)
    payload = api._request.call_args.kwargs["json"]
    assert payload["hvacTemp"] == "20.0"
    assert payload["ignitionDuration"] == 5
    assert payload["drvSeatLoc"] == "R"
    assert payload["seatClimateInfo"] == {
        "psgSeatClimateState": 6,
        "drvSeatClimateState": 8,
    }


@pytest.mark.parametrize(
    "method,args,capability,expected",
    [
        (
            "charge_port_action",
            (CHARGE_PORT_ACTION.OPEN,),
            {"serviceOption": {"chargePortDoorOption": 1}},
            "open",
        ),
        (
            "charge_port_action",
            (CHARGE_PORT_ACTION.CLOSE,),
            {"serviceOption": {"chargePortDoorOption": 1}},
            "close",
        ),
        ("start_hazard_lights", (), {"option": {"lightOnlyAvailable": 1}}, "on"),
        (
            "start_hazard_lights_and_horn",
            (),
            {
                "option": {"hornLightAvailable": 1},
                "serviceOption": {"remoteLightControlOption": 1},
            },
            "on",
        ),
        (
            "start_hazard_lights_and_horn",
            (),
            {
                "option": {"hornLightAvailable": 1},
                "serviceOption": {"remoteLightControlOption": 0},
            },
            "lowbeam",
        ),
    ],
)
def test_optional_controls_require_reported_profile_capability(
    method, args, capability, expected
):
    api, token, vehicle = setup()
    with pytest.raises(UnsupportedControlError):
        getattr(api, method)(token, vehicle, *args)
    api._request.assert_not_called()
    api._vehicle_profiles[vehicle.id].update(capability)
    getattr(api, method)(token, vehicle, *args)
    assert api._request.call_args.kwargs["json"] == {"command": expected}


@pytest.mark.parametrize(
    "method,args,field,value",
    [
        ("set_charging_current", (2,), "chargingCurrent", 2),
        ("set_vehicle_to_load_discharge_limit", (40,), "dischargingLimit", 40),
    ],
)
def test_evidenced_charge_setting_commands(method, args, field, value):
    api, token, vehicle = setup()
    api._vehicle_profiles[vehicle.id]["serviceOption"] = {
        "chargingCurrentSupport": 1,
        "v2LSocSetServiceOption": 1,
    }
    getattr(api, method)(token, vehicle, *args)
    assert api._request.call_args.kwargs["json"] == {field: value}
    assert (
        api.check_action_status(token, vehicle, "synthetic-action")
        == ORDER_STATUS.PENDING
    )


def test_all_window_open_and_unproved_partial_rejection():
    api, token, vehicle = setup()
    api._vehicle_profiles[vehicle.id]["serviceOption"] = {"windowControlOption": 1}
    options = WindowRequestOptions(
        front_left=WINDOW_STATE.OPEN,
        front_right=WINDOW_STATE.OPEN,
        back_left=WINDOW_STATE.OPEN,
        back_right=WINDOW_STATE.OPEN,
    )
    api.set_windows_state(token, vehicle, options)
    assert api._request.call_args.kwargs["json"] == {"command": "open"}
    options.back_left = None
    api._request.reset_mock()
    with pytest.raises(UnsupportedControlError):
        api.set_windows_state(token, vehicle, options)
    api._request.assert_not_called()


def test_schedule_cloud_snapshot_preserves_unspecified_fields_and_input():
    api, token, vehicle = setup()
    detail = {
        "reservChargeSet": True,
        "reservInfo": {"day": [1, 3], "time": {"time": "0830", "timeSection": 0}},
        "reservFatcSet": {
            "airCtrl": 1,
            "airTemp": {"value": "0CH", "unit": 0, "hvacTempType": 1},
            "heating1": 1,
            "defrost": True,
        },
    }
    current = {
        "reservFlag": 1,
        "reservChargeInfo": {"reservChargeInfoDetail": detail},
        "reservChargeInfo2": {"reservChargeInfoDetail": copy.deepcopy(detail)},
        "offPeakPowerInfo": {
            "offPeakPowerFlag": 2,
            "offPeakPowerTime1": {
                "starttime": {"time": "1000", "timeSection": 1},
                "endtime": {"time": "0700", "timeSection": 0},
            },
        },
    }
    before = copy.deepcopy(current)
    api._payload = lambda result: result["resMsg"]
    api._request.side_effect = [
        {"controlToken": "synthetic-control", "expiresTime": 60},
        {"resMsg": current},
        {"msgId": "synthetic-action"},
    ]
    api.schedule_charging_and_climate(
        token, vehicle, ScheduleChargingClimateRequestOptions(charging_enabled=False)
    )
    payload = api._request.call_args.kwargs["json"]
    assert payload["reservFlag"] == 0
    assert payload["offPeakPowerInfo"] == before["offPeakPowerInfo"]
    assert payload["reservChargeInfo1"]["reservInfo"] == detail["reservInfo"]
    assert payload["reservChargeInfo1"]["reservFatcSet"]["airTemp"]["value"] == "20.0"
    assert current == before


def test_valet_uses_non_ccs2_endpoint_and_explicit_capability():
    api, token, vehicle = setup()
    api._vehicle_profiles[vehicle.id]["serviceOption"] = {"remoteValetActOption": 1}
    api.valet_mode_action(token, vehicle, VALET_MODE_ACTION.DEACTIVATE)
    assert api._request.call_args.args[1].endswith("/control/valet")
    assert "/ccs2/" not in api._request.call_args.args[1]
    assert api._request.call_args.kwargs["json"] == {"action": "deactivate"}


def test_boolean_capability_and_float_current_rejected_before_io():
    api, token, vehicle = setup()
    api._vehicle_profiles[vehicle.id]["serviceOption"] = {
        "chargingCurrentSupport": True
    }
    with pytest.raises(UnsupportedControlError):
        api.set_charging_current(token, vehicle, 1)
    api._vehicle_profiles[vehicle.id]["serviceOption"]["chargingCurrentSupport"] = 1
    with pytest.raises(ValueError):
        api.set_charging_current(token, vehicle, 1.0)
    api._request.assert_not_called()


def test_navigation_exact_envelope_and_coordinate_validation():
    api, token, vehicle = setup()
    api.set_navigation(token, vehicle, [POIInfo(coord=POICoord(lat=35.0, lon=139.0))])
    payload = api._request.call_args.kwargs["json"]
    assert set(payload) == {"poiInfoList"}
    assert payload["poiInfoList"][0]["waypointID"] == 0
    assert api._request.call_args.args[1].endswith("/location/routes")
    api._request.reset_mock()
    with pytest.raises(ValueError):
        api.set_navigation(
            token, vehicle, [POIInfo(coord=POICoord(lat=float("nan"), lon=139.0))]
        )
    api._request.assert_not_called()


@pytest.mark.parametrize("heating", [0, 1, 2, 3])
def test_ha_combined_heating_modes_preserved(heating):
    api, token, vehicle = setup()
    api.start_climate(
        token, vehicle, ClimateRequestOptions(set_temp=20, heating=heating)
    )
    assert api._request.call_args.kwargs["json"]["heating1"] == heating


def test_separate_heating_profile_uses_exact_wire_fields():
    api, token, vehicle = setup()
    api._vehicle_profiles[vehicle.id]["option"]["strgWhlHeatOption"] = "2"
    api.start_climate(
        token,
        vehicle,
        ClimateRequestOptions(
            set_temp=20, steering_wheel=2, side_rear_mirror_heating=1
        ),
    )
    payload = api._request.call_args.kwargs["json"]
    assert payload["heating1"] is None
    assert payload["strgWhlHeating"] == 2
    assert payload["sideRearMirrorHeating"] == 1


def test_nullable_rhd_independent_window_and_vent_fields():
    api, token, vehicle = setup()
    api._vehicle_profiles[vehicle.id].update(
        {
            "option": {"drvSeatLoc": "R", "windowSafetyOption2": 3},
            "serviceOption": {"windowControlOption": 1},
        }
    )
    api.set_windows_state(
        token, vehicle, WindowRequestOptions(front_right=WINDOW_STATE.VENTILATION)
    )
    payload = api._request.call_args.kwargs["json"]
    assert payload["drvSeatWindow"] == 2
    assert payload["psgSeatWindow"] is None
    assert payload["rlSeatWindow"] is None
    assert api._request.call_args.args[1].endswith("/control/windowcurtain")


def test_aggregate_keeps_shared_real_mqtt_results_until_both_children_complete():
    api, token, vehicle = setup()
    token.access_token = "Bearer synthetic-access"
    helper = JapanMqttSession(token, vehicle.id, "synthetic-uuid", dict)
    helper._ready = True
    helper._topics = ("synthetic-vss", "synthetic-res", "synthetic-connection")
    helper.prepare = Mock()
    api._mqtt_for_vehicle.return_value = helper
    api._request.side_effect = [
        {"controlToken": "synthetic-control", "expiresTime": 60},
        {"msgId": "child-one"},
        {"msgId": "child-two"},
    ]
    api._japan_command(token, vehicle, "reservation/charge", {})
    api._japan_command(token, vehicle, "reservation/hvac", {})
    api._remember_japan_action("aggregate", vehicle.id, ("child-one", "child-two"))
    for child in ("child-one", "child-two"):
        helper._on_message(
            None,
            None,
            SimpleNamespace(
                retain=False,
                topic="synthetic-res",
                payload=json.dumps({"sid": child, "res-msg": "S"}).encode(),
            ),
        )
    assert api.check_action_status(token, vehicle, "aggregate") == ORDER_STATUS.SUCCESS
    assert helper.closed
    assert api.check_action_status(token, vehicle, "aggregate") == ORDER_STATUS.SUCCESS


def test_ev5_flat_schedule_uses_root_app_mode_and_scope_specific_wire_contracts():
    api, token, vehicle = setup()
    api._vehicle_profiles[vehicle.id]["appMode"] = "EV5"
    detail = {
        "reservChargeSet": True,
        "reservInfo": {"day": [1], "time": {"time": "0800", "timeSection": 0}},
        "reservFatcSet": {
            "airCtrl": 1,
            "defrost": False,
            "airTemp": {"value": "0CH", "unit": 0, "hvacTempType": 1},
        },
    }
    current = {
        "reservFlag": 1,
        "reservChargeInfo": {"reservChargeInfoDetail": detail},
        "reservChargeInfo2": {"reservChargeInfoDetail": copy.deepcopy(detail)},
        "offPeakPowerInfo": {
            "offPeakPowerFlag": 2,
            "offPeakPowerTime1": {
                "starttime": {"time": "1000", "timeSection": 1},
                "endtime": {"time": "0700", "timeSection": 0},
            },
        },
    }
    api._payload = lambda result: result["resMsg"]
    api._request.side_effect = [
        {"controlToken": "synthetic-control", "expiresTime": 60},
        {"resMsg": current},
    ]
    api._japan_command = Mock(side_effect=["charge-id", "climate-id"])
    api.schedule_charging_and_climate(
        token,
        vehicle,
        ScheduleChargingClimateRequestOptions(charging_enabled=False, temperature=20),
    )
    charge, climate = api._japan_command.call_args_list
    assert charge.args[2] == "reservation/charge"
    assert set(charge.args[3]) == {
        "reservFlag",
        "offpeakPowerFlag",
        "reservStartTime",
        "reservEndTime",
    }
    assert climate.args[2] == "reservation/hvac"
    assert set(climate.args[3]) == {"reservedHVACInfo1", "reservedHVACInfo2"}
    assert (
        climate.args[3]["reservedHVACInfo1"]["reservHVACSet"]["airTemp"]["value"]
        == "0CH"
    )


@pytest.mark.parametrize(
    "state,command", [(WINDOW_STATE.OPEN, "open"), (WINDOW_STATE.CLOSED, "close")]
)
def test_uniform_window_wire_values_are_not_enum_names(state, command):
    api, token, vehicle = setup()
    api._vehicle_profiles[vehicle.id]["serviceOption"] = {"windowControlOption": 1}
    api.set_windows_state(
        token,
        vehicle,
        WindowRequestOptions(
            front_left=state, front_right=state, back_left=state, back_right=state
        ),
    )
    request = api._request.call_args
    assert request.args == (
        "POST",
        "/api/v2/spa/vehicles/synthetic-car/ccs2/control/window",
    )
    assert request.kwargs["json"] == {"command": command}


def test_command_values_match_independent_dex_constructor_facts():
    facts = json.loads(
        (
            Path(__file__).parent / "fixtures/jp_android_1_0_17_command_values.json"
        ).read_text()
    )["command_values"]
    cases = [
        ("DOOR_LOCK", "lock_action", (VEHICLE_LOCK_ACTION.LOCK,)),
        ("DOOR_UNLOCK", "lock_action", (VEHICLE_LOCK_ACTION.UNLOCK,)),
        ("PORT_DOOR_OPEN", "charge_port_action", (CHARGE_PORT_ACTION.OPEN,)),
        ("PORT_DOOR_CLOSE", "charge_port_action", (CHARGE_PORT_ACTION.CLOSE,)),
        ("CHARGE_START", "start_charge", ()),
        ("CHARGE_STOP", "stop_charge", ()),
        ("EV_ENGINE_STOP", "stop_climate", ()),
        ("LIGHT", "start_hazard_lights", ()),
    ]
    for enum_name, method, args in cases:
        api, token, vehicle = setup()
        api._vehicle_profiles[vehicle.id].update(
            {
                "serviceOption": {"chargePortDoorOption": 1},
                "option": {"lightOnlyAvailable": 1},
            }
        )
        getattr(api, method)(token, vehicle, *args)
        assert api._request.call_args.kwargs["json"] == {"command": facts[enum_name]}
    for option, enum_name in [(0, "HORN_LAMP"), (1, "HORN_LIGHT")]:
        api, token, vehicle = setup()
        api._vehicle_profiles[vehicle.id].update(
            {
                "serviceOption": {"remoteLightControlOption": option},
                "option": {"hornLightAvailable": 1},
            }
        )
        api.start_hazard_lights_and_horn(token, vehicle)
        assert api._request.call_args.kwargs["json"] == {"command": facts[enum_name]}
    for state, enum_name in [
        (WINDOW_STATE.OPEN, "WINDOW_OPEN"),
        (WINDOW_STATE.CLOSED, "WINDOW_CLOSE"),
    ]:
        api, token, vehicle = setup()
        api._vehicle_profiles[vehicle.id]["serviceOption"] = {"windowControlOption": 1}
        api.set_windows_state(
            token,
            vehicle,
            WindowRequestOptions(
                front_left=state, front_right=state, back_left=state, back_right=state
            ),
        )
        assert api._request.call_args.kwargs["json"] == {"command": facts[enum_name]}


@pytest.mark.parametrize(
    "status,stage",
    [
        (ORDER_STATUS.PENDING, "waiting"),
        (ORDER_STATUS.SUCCESS, "success"),
        (ORDER_STATUS.FAILED, "failed"),
        (ORDER_STATUS.TIMEOUT, "timeout"),
    ],
)
def test_command_diagnostics_track_completion_without_identifiers(status, stage):
    api, token, vehicle = setup()
    action = api.lock_action(token, vehicle, VEHICLE_LOCK_ACTION.UNLOCK)
    assert api._japan_command_diagnostics == {
        "endpoint": "door",
        "command": "open",
        "stage": "waiting",
    }
    api.helper.status.return_value = status
    api.check_action_status(token, vehicle, action)
    assert api._japan_command_diagnostics["stage"] == stage
    serialized = json.dumps(api._japan_command_diagnostics)
    for secret in (
        vehicle.id,
        action,
        token.pin,
        token.device_id,
        "Authorization",
        "synthetic-access",
        "synthetic-control",
    ):
        assert secret not in serialized


@pytest.mark.parametrize(
    "failure_at,expected",
    [
        ("pin", "pin_validation"),
        ("mqtt", "mqtt_setup"),
        ("http", "http_submitting"),
        ("status", "waiting"),
    ],
)
def test_command_diagnostics_preserve_sanitized_failure_stage(failure_at, expected):
    api, token, vehicle = setup()
    error = InvalidAPIResponseError("private-token-and-location")
    if failure_at == "pin":
        api._request.side_effect = error
    elif failure_at == "mqtt":
        api.helper.prepare.side_effect = error
    elif failure_at == "http":
        api._request.side_effect = [
            {"controlToken": "synthetic-control", "expiresTime": 60},
            error,
        ]
    with pytest.raises(InvalidAPIResponseError):
        action = api.lock_action(token, vehicle, VEHICLE_LOCK_ACTION.UNLOCK)
        if failure_at == "status":
            api.helper.status.side_effect = error
            api.check_action_status(token, vehicle, action)
    diagnostic = api._japan_command_diagnostics
    assert diagnostic["stage"] == "failed"
    assert diagnostic["failed_stage"] == expected
    assert diagnostic["failure_category"] == "invalid_response"
    assert "private-token-and-location" not in json.dumps(diagnostic)


def test_command_diagnostics_do_not_copy_unknown_endpoint_or_payload():
    api, token, vehicle = setup()
    api._japan_command(
        token, vehicle, "private-location-token", {"command": "private-secret"}
    )
    assert api._japan_command_diagnostics == {"endpoint": "other", "stage": "waiting"}


def test_expired_control_grant_is_renewed_after_slow_mqtt_setup(monkeypatch):
    api, token, vehicle = setup()
    clock = [100.0]
    monkeypatch.setattr(
        "hyundai_kia_connect_api.japan_commands.time.time", lambda: clock[0]
    )
    token.control_token = "Bearer synthetic-old-control"
    token.control_token_expiry = 101
    api.helper.prepare.side_effect = lambda: clock.__setitem__(0, 200.0)
    api.lock_action(token, vehicle, VEHICLE_LOCK_ACTION.UNLOCK)
    grant, command = api._request.call_args_list
    assert grant.args == ("PUT", "/api/v1/user/pin")
    assert command.kwargs["headers"]["Authorization"] == "Bearer synthetic-control"
    assert token.control_token_expiry == 260


@pytest.mark.parametrize("expiry", [None, "300", True, float("nan"), float("inf")])
def test_malformed_cached_grant_expiry_is_renewed(expiry):
    api, token, vehicle = setup()
    token.control_token = "Bearer synthetic-old-control"
    token.control_token_expiry = expiry
    api.start_charge(token, vehicle)
    assert api._request.call_args_list[0].args == ("PUT", "/api/v1/user/pin")


def test_action_eviction_keeps_shared_pending_mqtt_session():
    api, _, vehicle = setup()
    api._remember_japan_action("oldest", vehicle.id, api.helper)
    for index in range(127):
        api._remember_japan_action(f"pending-{index}", vehicle.id, api.helper)
    api._remember_japan_action("newest", vehicle.id, api.helper)
    api.helper.close.assert_not_called()
    assert len(api._japan_actions) == 128
    assert "oldest" not in api._japan_actions


def test_replacing_tracked_action_does_not_evict_unrelated_action():
    api, _, vehicle = setup()
    for index in range(128):
        api._remember_japan_action(f"action-{index}", vehicle.id, api.helper)
    api._remember_japan_action("action-127", vehicle.id, api.helper)
    assert len(api._japan_actions) == 128
    assert "action-0" in api._japan_actions
    api.helper.close.assert_not_called()


def test_composite_completion_shares_one_wait_budget(monkeypatch):
    api, token, vehicle = setup()
    clock = [0.0]
    monkeypatch.setattr(
        "hyundai_kia_connect_api.japan_commands.time.monotonic", lambda: clock[0]
    )
    api._remember_japan_action("first", vehicle.id, api.helper)
    api._remember_japan_action("second", vehicle.id, api.helper)
    api._remember_japan_action("aggregate", vehicle.id, ("first", "second"))

    def status(action_id, timeout):
        if action_id == "first":
            clock[0] += 40
            return ORDER_STATUS.SUCCESS
        assert timeout == 20
        clock[0] += timeout
        return ORDER_STATUS.TIMEOUT

    api.helper.status.side_effect = status
    assert (
        api.check_action_status(token, vehicle, "aggregate", True, 60)
        == ORDER_STATUS.TIMEOUT
    )
    assert clock[0] == 60
    api.helper.close.assert_called_once()


@pytest.mark.parametrize("timeout", [0, -1, True, None, float("nan")])
def test_composite_rejects_invalid_wait_before_polling(timeout):
    api, token, vehicle = setup()
    api._remember_japan_action("first", vehicle.id, api.helper)
    api._remember_japan_action("aggregate", vehicle.id, ("first",))
    with pytest.raises(ValueError, match="positive timeout"):
        api.check_action_status(token, vehicle, "aggregate", True, timeout)
    api.helper.status.assert_not_called()


@pytest.mark.parametrize("missing", ["defrost", "reservChargeSet"])
def test_flat_schedule_validates_both_envelopes_before_either_write(missing):
    api, token, vehicle = setup()
    api._vehicle_profiles[vehicle.id]["appMode"] = "EV5"
    detail = {
        "reservChargeSet": True,
        "reservInfo": {"day": [1], "time": {"time": "0800", "timeSection": 0}},
        "reservFatcSet": {
            "airCtrl": 1,
            "defrost": False,
            "airTemp": {"value": "0CH", "unit": 0, "hvacTempType": 1},
        },
    }
    if missing == "defrost":
        del detail["reservFatcSet"][missing]
    else:
        del detail[missing]
    current = {
        "reservFlag": 1,
        "reservChargeInfo": {"reservChargeInfoDetail": detail},
        "reservChargeInfo2": {"reservChargeInfoDetail": copy.deepcopy(detail)},
        "offPeakPowerInfo": {
            "offPeakPowerFlag": 2,
            "offPeakPowerTime1": {
                "starttime": {"time": "1000", "timeSection": 1},
                "endtime": {"time": "0700", "timeSection": 0},
            },
        },
    }
    api._payload = lambda result: result["resMsg"]
    api._request.side_effect = [
        {"controlToken": "synthetic-control", "expiresTime": 60},
        {"resMsg": current},
    ]
    api._japan_command = Mock()
    with pytest.raises(InvalidAPIResponseError, match="flat climate schedule"):
        api.schedule_charging_and_climate(
            token,
            vehicle,
            ScheduleChargingClimateRequestOptions(
                charging_enabled=False, temperature=20
            ),
        )
    api._japan_command.assert_not_called()
    assert [call.args[0] for call in api._request.call_args_list] == ["PUT", "GET"]


@pytest.mark.parametrize("unrelated_pending", [False, True])
def test_composite_budget_exhaustion_retires_unpolled_children(
    monkeypatch, unrelated_pending
):
    api, token, vehicle = setup()
    clock = [0.0]
    monkeypatch.setattr(
        "hyundai_kia_connect_api.japan_commands.time.monotonic", lambda: clock[0]
    )
    api._remember_japan_action("first", vehicle.id, api.helper)
    api._remember_japan_action("second", vehicle.id, api.helper)
    api._remember_japan_action("aggregate", vehicle.id, ("first", "second"))
    if unrelated_pending:
        api._remember_japan_action("unrelated", vehicle.id, api.helper)

    def status(action_id, timeout):
        if action_id == "first":
            clock[0] += timeout
            return ORDER_STATUS.TIMEOUT
        assert action_id == "second"
        assert timeout == 0
        return ORDER_STATUS.PENDING

    api.helper.status.side_effect = status
    assert (
        api.check_action_status(token, vehicle, "aggregate", True, 60)
        == ORDER_STATUS.TIMEOUT
    )
    assert api._japan_actions["second"][2] == ORDER_STATUS.TIMEOUT
    assert api.check_action_status(token, vehicle, "second") == ORDER_STATUS.TIMEOUT
    assert api.helper.status.call_count == 2
    assert api.check_action_status(token, vehicle, "aggregate") == ORDER_STATUS.TIMEOUT
    if unrelated_pending:
        api.helper.close.assert_not_called()
        assert api._japan_actions["unrelated"][2] is None
    else:
        api.helper.close.assert_called_once()


@pytest.mark.parametrize("unrelated_pending", [False, True])
def test_failed_composite_retires_unconfirmed_children_without_claiming_failure(
    unrelated_pending,
):
    api, token, vehicle = setup()
    api._remember_japan_action("first", vehicle.id, api.helper)
    api._remember_japan_action("second", vehicle.id, api.helper)
    api._remember_japan_action("aggregate", vehicle.id, ("first", "second"))
    api._japan_diagnostic_action_id = "second"
    api._japan_command_diagnostics = {"stage": "waiting"}
    if unrelated_pending:
        api._remember_japan_action("unrelated", vehicle.id, api.helper)
    api.helper.status.side_effect = [ORDER_STATUS.FAILED, ORDER_STATUS.PENDING]
    assert api.check_action_status(token, vehicle, "aggregate") == ORDER_STATUS.FAILED
    assert api.check_action_status(token, vehicle, "second") == ORDER_STATUS.UNKNOWN
    assert api._japan_command_diagnostics["stage"] == "unconfirmed"
    assert api.helper.status.call_count == 2
    if unrelated_pending:
        api.helper.close.assert_not_called()
        assert api._japan_actions["unrelated"][2] is None
    else:
        api.helper.close.assert_called_once()
