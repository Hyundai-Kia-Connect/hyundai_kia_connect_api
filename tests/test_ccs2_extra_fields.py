"""CCS2 state.Vehicle fields not previously mapped (Body/Cabin/Electronics/
Service/Location/Green extras). Inventory from a live Genesis AU GV70 plus the
EU EV3/Sportage PHEV fixtures."""

import datetime as dt

import pytest

from hyundai_kia_connect_api.ApiImplType1 import ApiImplType1
from hyundai_kia_connect_api.const import ENGINE_TYPES
from hyundai_kia_connect_api.Vehicle import Vehicle
from tests.fixture_helpers import load_fixture


@pytest.fixture
def api() -> ApiImplType1:
    a = ApiImplType1.__new__(ApiImplType1)
    a.data_timezone = dt.UTC
    a.temperature_range = [x * 0.5 for x in range(28, 60)]
    return a


def _set(d: dict, path: str, value) -> None:
    keys = path.split(".")
    for k in keys[:-1]:
        d = d.setdefault(k, {})
    d[keys[-1]] = value


def _gv70_like_state() -> dict:
    state = load_fixture("eu_kia_ev9_2024_ccs2.json")
    for path, value in {
        "Body.Lights.DischargeAlert.State": 1,
        "Body.Lights.Front.Left.TurnSignal.LampState": 1,
        "Body.Lights.Front.Right.TurnSignal.LampState": 0,
        "Body.Windshield.Front.Heat.State": 1,
        "Cabin.HVAC.Vent.FineDust.State": 2,
        "Cabin.HVAC.Vent.FineDust.Level": 3,
        "Cabin.HVAC.Vent.AirCleaning.Indicator": 1,
        "Cabin.HVAC.Vent.AirCleaning.SymbolColor": 0,
        "Cabin.HVAC.Temperature.RangeType": 1,
        "Cabin.Seat.Row1.Driver.ArmRest.Heat": 1,
        "Cabin.Seat.Row1.Passenger.ArmRest.Heat": 0,
        "Cabin.Seat.Row2.Left.ArmRest.Heat": 0,
        "Cabin.Seat.Row2.Right.ArmRest.Heat": 1,
        "Cabin.Seat.Row3.Left.Climate.State": 6,
        "Cabin.Seat.Row3.Right.Climate.State": 2,
        "Cabin.SteeringWheel.Heat.RemoteControl.Step": 2,
        "Cabin.RestMode.State": 0,
        "Drivetrain.DrivingReady": 0,
        "Drivetrain.ExhaustSystem.UreaLevel": 0,
        "Drivetrain.Transmission.ParkingPosition": 1,
        "Drivetrain.Transmission.GearPosition": 0,
        "Drivetrain.FuelSystem.AverageFuelEconomy.Accumulated": 18,
        "Drivetrain.FuelSystem.AverageFuelEconomy.Drive": 19.3,
        "Drivetrain.FuelSystem.AverageFuelEconomy.AfterRefuel": 16.6,
        "Drivetrain.FuelSystem.AverageFuelEconomy.Unit": 5,
        "Chassis.Driving.WheelDriveType.ControllerState1": 1,
        "Chassis.Driving.WheelDriveType.ControllerState2": 1,
        "Electronics.Battery.Charging.WarningLevel": 65,
        "Electronics.Battery.PowerStateAlert.ClassC": 2,
        "Electronics.AutoCut.PowerMode": 2,
        "Electronics.AutoCut.DeliveryMode": 2,
        "Electronics.AutoCut.BatteryPreWarning": 0,
        "Electronics.PowerSupply.Ignition1": 0,
        "Service.ConnectedCar.RemoteControl.Available": 1,
        "Service.ConnectedCar.RemoteControl.WaitingTime": 336,
        "Service.ConnectedCar.ActiveAlert.Available": 0,
        "ConnectedService.OTA.ControllerStatus": 0,
        "Version": "CCU2026101304441301",
        "Location.Heading": 250,
        "Location.Speed.Unit": 0,
        "Location.Speed.Value": 11.5,
        "Location.GeoCoord.Altitude": 0.99,
    }.items():
        _set(state, path, value)
    state.pop("Green", None)
    return state


def test_body_cabin_and_electronics_extras(api):
    v = Vehicle()
    v.engine_type = ENGINE_TYPES.ICE
    api._update_vehicle_properties_ccs2(v, _gv70_like_state())
    assert v.lights_discharge_alert_is_on is True
    assert v.turn_signal_left_front_is_on is True
    assert v.turn_signal_right_front_is_on is False
    assert v.windshield_front_heater_is_on is True
    assert v.air_cleaning_is_on is True
    assert v.air_cleaning_symbol_color == 0
    assert (v.fine_dust_state, v.fine_dust_level) == (2, 3)
    assert v.hvac_temperature_range_type == 1
    assert v.rest_mode_is_on is False
    assert v.front_left_armrest_heater_is_on is True
    assert v.front_right_armrest_heater_is_on is False
    assert v.rear_right_armrest_heater_is_on is True
    assert v.third_row_left_seat_status == "Low Heat"
    assert v.third_row_right_seat_status == "Off"
    assert v.steering_wheel_heat_step == 2
    assert v.drivetrain_driving_ready is False
    assert v.urea_level == 0
    assert v.transmission_in_park is True
    assert v.gear_position == 0
    assert v.average_fuel_economy_accumulated == 18.0
    assert v.average_fuel_economy_drive == 19.3
    assert v.average_fuel_economy_after_refuel == 16.6
    assert v.average_fuel_economy_unit == 5
    assert (v.awd_controller_state_1, v.awd_controller_state_2) == (1, 1)
    assert v.battery_12v_charging_warning_level == 65
    assert v.battery_12v_power_state_alert == 2
    assert (v.auto_cut_power_mode, v.auto_cut_delivery_mode) == (2, 2)
    assert v.auto_cut_battery_prewarning_on is False
    assert v.ignition1_on is False
    assert v.remote_control_available is True
    assert v.remote_control_waiting_time == 336
    assert v.active_alert_available is False
    assert v.ota_controller_status == 0
    assert v.ccu_version == "CCU2026101304441301"
    assert v.location_heading == 250.0
    assert v.location_speed == 11.5
    assert v.location_speed_unit == 0
    assert v.location_altitude == 0.99
    # ICE: no Green block -> all EV extras stay None
    assert v.ev_battery_conditioning_is_on is None
    assert v.ev_plug_and_charge_certificates is None
    assert v.ev_departure_climate_enabled is None


def test_absent_fields_stay_none(api):
    v = Vehicle()
    state = load_fixture("eu_kia_ev9_2024_ccs2.json")
    api._update_vehicle_properties_ccs2(v, state)
    for name in (
        "lights_discharge_alert_is_on",
        "frunk_fault_is_on",
        "rest_mode_is_on",
        "third_row_left_seat_status",
        "awd_controller_state_1",
        "urea_level",
        "ota_controller_status",
        "location_service_state",
        "ev_v2g_mode",
        "ev_plug_and_charge_mode",
    ):
        assert getattr(v, name) is None, name


def test_green_extras_from_ev3_fixture(api):
    v = Vehicle()
    v.engine_type = ENGINE_TYPES.EV
    state = load_fixture("eu_kia_ev3_2025_ccs2.json")
    api._update_vehicle_properties_ccs2(v, state)
    assert v.ev_battery_conditioning_is_on is False
    assert v.ev_battery_precondition_temperature_level == 1
    assert v.ev_charging_sequence_details == 12
    assert v.ev_charging_sequence_subcode == 2
    # 31:63 sentinel -> None
    assert v.ev_charge_expected_start_time is None
    assert v.ev_charge_expected_end_time is None
    assert v.ev_charge_complete_alarm_before_10min is True
    assert v.ev_charge_complete_alarm_before_20min is False
    assert v.ev_charge_complete_alarm_off is False
    assert v.ev_driving_history_average == 16.6
    assert v.ev_driving_history_unit == 1
    assert v.ev_driving_ready is False
    assert v.ev_v2g_mode == 0
    assert v.ev_v2l_discharge_dte == 94
    assert v.ev_v2l_discharge_remain_time == 0
    assert v.ev_driving_range_invalid is False
    assert v.ev_plug_and_charge_mode == 1
    assert v.ev_plug_and_charge_changeable is True
    assert v.ev_plug_and_charge_selected_cert == 0
    assert len(v.ev_plug_and_charge_certificates) == 5
    assert v.ev_plug_and_charge_certificates[0]["Company"] == ""
    assert v.ev_power_consumption_prediction_climate == 0.0
    assert v.ev_departure_climate_enabled is True
    assert v.ev_departure_climate_defrost is False
    assert v.ev_departure_climate_temperature == 20.0
    assert v.ev_departure_climate_temperature_unit == "°C"
    assert v.frunk_fault_is_on is False
    assert v.third_row_left_seat_status == "Off"
    assert v.location_service_state == 0


def test_phev_fuel_range_and_soc_raw(api):
    v = Vehicle()
    v.engine_type = ENGINE_TYPES.PHEV
    state = load_fixture("eu_kia_sportage_phev_2027_ccs2.json")
    api._update_vehicle_properties_ccs2(v, state)
    assert v.fuel_driving_range == 551.0
    assert v.ev_battery_percentage_raw == 72.5
    assert isinstance(v.ev_charging_sequence_details, int)
    assert isinstance(v.ev_charging_sequence_subcode, int)
