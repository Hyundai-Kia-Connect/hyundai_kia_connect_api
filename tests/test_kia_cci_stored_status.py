"""Tests for KiaCciApiEU.update_vehicle_with_cached_state — the shared
CCS2 property parser applied to live Kia GSPA stored-status fixtures
(EV6 2024, PV5 2026; zero network). The EV6 fixture leaf values are
synthetic (replaced by the volunteer before sharing); the PV5 fixture
keeps the real values except the identifying leaves (Date, Version,
Drivetrain.Odometer, envelope msgId/lastUpdateTime). Structure and key
names are real in both."""

from unittest.mock import patch

import pytest

from hyundai_kia_connect_api.KiaCciApiEU import KiaCciApiEU
from hyundai_kia_connect_api.Token import Token
from hyundai_kia_connect_api.Vehicle import Vehicle
from tests.fixture_helpers import load_fixture

FIXTURE = "eu_kia_gspa_ev6_2024_stored_status.json"
PV5_FIXTURE = "eu_kia_gspa_pv5_2026_stored_status.json"


@pytest.fixture
def api() -> KiaCciApiEU:
    return KiaCciApiEU.__new__(KiaCciApiEU)


@pytest.fixture
def vehicle() -> Vehicle:
    return Vehicle()


def test_ccs2_parser_on_kia_stored_status(api: KiaCciApiEU, vehicle: Vehicle) -> None:
    """The shared CCS2 parser handles the Kia EV6 state tree."""
    data = load_fixture(FIXTURE)
    api._update_vehicle_properties_ccs2(vehicle, data)

    assert vehicle.ev_battery_percentage == 70.0
    assert vehicle.car_battery_percentage == 70
    # Kia sends DrivingReady as 0/1, Hyundai as a bool — both falsy.
    assert not vehicle.engine_is_running
    assert vehicle.front_left_door_is_locked is True
    assert vehicle.is_locked is True
    # HVAC temperature is 'OFF' → no temperature set.
    assert vehicle.air_temperature is None
    # Hyundai-specific sections absent from the Kia tree stay None
    # (graceful degradation, not masking): OutsideTemperature is not in
    # the EV6 payload.
    assert vehicle.outside_temperature is None


def test_ccs2_parser_on_pv5_stored_status(api: KiaCciApiEU, vehicle: Vehicle) -> None:
    """The shared CCS2 parser handles the second live Kia schema (PV5
    Passenger, 2026). Sections absent from the PV5 payload (Location,
    SoH, charging door, seat climate) stay None — no crash — while the
    PV5-only leaves (OutsideTemperature, pack voltage, chiller, water
    temperature, V2L, sunroof) parse."""
    import datetime as dt

    from hyundai_kia_connect_api.const import PressureUnit

    data = load_fixture(PV5_FIXTURE)
    api._update_vehicle_properties_ccs2(vehicle, data)

    assert vehicle.ev_battery_percentage == 56.5
    assert vehicle.car_battery_percentage == 88
    # PV5 carries OutsideTemperature (EV6 does not).
    assert vehicle.outside_temperature == 19.5
    # OffPeakTime Mode=2 (live): schedule on, off-peak-only off.
    assert vehicle.ev_off_peak_start_time == dt.time(14, 20)
    assert vehicle.ev_off_peak_end_time == dt.time(17, 30)
    assert vehicle.ev_schedule_charge_enabled is True
    assert vehicle.ev_off_peak_charge_only_enabled is False
    # PV5-only BMS/SmartGrid leaves.
    assert vehicle.ev_battery_pack_voltage == 413
    assert vehicle.ev_battery_chiller_rpm == 0
    assert vehicle.ev_battery_water_temperature == 20
    assert vehicle.ev_v2l_discharge_limit == 20
    assert vehicle.sunroof_is_open is True
    assert vehicle.tire_pressure_unit == PressureUnit.BAR
    assert vehicle.is_locked is True
    # HVAC temperature is 'OFF' -> no temperature set.
    assert vehicle.air_temperature is None
    # Absent from the PV5 payload -> None (graceful degradation, not
    # masking): SoH, charging door.
    assert vehicle.ev_battery_soh_percentage is None
    assert vehicle.ev_battery_is_plugged_in == 0


def test_update_vehicle_with_cached_state(api: KiaCciApiEU, vehicle: Vehicle) -> None:
    """update_vehicle_with_cached_state feeds stored-status state.Vehicle
    to the shared parser and does not call the Hyundai-only driving
    parsers (they are not part of the Kia API surface)."""
    state_vehicle = {
        k: v for k, v in load_fixture(FIXTURE).items() if k != "_fixture_meta"
    }
    stored = {"serviceNo": "RVS-K 1789000000000", "state": {"Vehicle": state_vehicle}}
    with patch.object(
        KiaCciApiEU, "get_stored_status", return_value=stored
    ) as get_status:
        token = Token()
        token.access_token = "ccs-token"
        api.update_vehicle_with_cached_state(token, vehicle)
    get_status.assert_called_once()

    assert vehicle.ev_battery_percentage == 70.0
    assert vehicle.car_battery_percentage == 70


def test_update_vehicle_with_cached_state_no_ccs_token(
    api: KiaCciApiEU, vehicle: Vehicle
) -> None:
    """A token without CCS credentials raises APIError before any call."""
    from hyundai_kia_connect_api.exceptions import APIError

    token = Token()
    with (
        patch.object(KiaCciApiEU, "get_stored_status") as get_status,
        pytest.raises(APIError),
    ):
        api.update_vehicle_with_cached_state(token, vehicle)
    get_status.assert_not_called()


def test_update_vehicle_with_cached_state_no_data(
    api: KiaCciApiEU, vehicle: Vehicle
) -> None:
    """A None stored-status response raises APIError."""
    from hyundai_kia_connect_api.exceptions import APIError

    with patch.object(KiaCciApiEU, "get_stored_status", return_value=None):
        token = Token()
        token.access_token = "ccs-token"
        with pytest.raises(APIError):
            api.update_vehicle_with_cached_state(token, vehicle)


def test_kia_api_has_no_hyundai_driving_parsers() -> None:
    """Driving info/history parsers stay Hyundai-specific (D7)."""
    assert not hasattr(KiaCciApiEU, "_get_driving_info")
    assert not hasattr(KiaCciApiEU, "_get_driving_history")
    assert not hasattr(KiaCciApiEU, "_parse_breakdowns")


def test_ccs2_parser_flat_schema_paths(api: KiaCciApiEU, vehicle: Vehicle) -> None:
    """Flat CCS2-schema paths parse (PV5/PV Passenger shape, live
    2026-09-19). The nested variants the parser previously read
    ("BatteryPack.Voltage", "Chiller.RPM", "Temperature.Water",
    "EnergyConsumption.*.Value", "OffPeakPower.*") appear in no captured
    payload and were removed."""
    import datetime as dt

    from hyundai_kia_connect_api.const import TEMPERATURE_UNITS

    state = {
        "Green": {
            "BatteryManagement": {
                "BatteryPackVoltage": 356.4,
                "ChillerRPM": 1200,
                "Temperature": {"CoolingWaterInlet": 18},
                "BatteryPreCondition": {"Status": 3, "TemperatureLevel": 2},
            },
            "PowerConsumption": {
                "Moment": {
                    "ClimateAirConditioning": 4.5,
                    "BatteryCooling": 0.0,
                    "BatteryHeater": 2.25,
                }
            },
            "Reservation": {
                "OffPeakTime": {
                    "StartHour": 22,
                    "StartMin": 0,
                    "EndHour": 1,
                    "EndMin": 30,
                    "Mode": 3,
                },
            },
        },
        "Body": {"Lights": {"Front": {"HeadLamp": {"SystemWarning": 0}}}},
    }
    api._update_vehicle_properties_ccs2(vehicle, state)

    assert vehicle.ev_battery_pack_voltage == 356
    assert vehicle.ev_battery_chiller_rpm == 1200
    assert vehicle.ev_battery_water_temperature == 18
    assert vehicle.ev_battery_water_temperature_unit == TEMPERATURE_UNITS[0]
    # Status 3 = on (kia_uvo #1823 mapping); winter mode stays unset.
    assert vehicle.ev_battery_precondition_enabled is True
    assert vehicle.ev_battery_winter_mode is None
    assert vehicle.ev_power_consumption_air_conditioning == 4.5
    assert vehicle.ev_power_consumption_battery_cooling == 0.0
    assert vehicle.ev_power_consumption_battery_heater == 2.25
    assert vehicle.ev_off_peak_start_time == dt.time(22, 0)
    assert vehicle.ev_off_peak_end_time == dt.time(1, 30)
    # Mode 3 = time-priority: schedule on, off-peak-only on.
    assert vehicle.ev_schedule_charge_enabled is True
    assert vehicle.ev_off_peak_charge_only_enabled is True
    assert vehicle.headlamp_status == 0


def test_ccs2_parser_offpeak_absent_stays_none(
    api: KiaCciApiEU, vehicle: Vehicle
) -> None:
    """No OffPeakTime block -> all off-peak fields stay None (no
    synthesised midnight window); HEV-style WinterModeOperation fallback
    maps to preconditioning."""
    state = {
        "Green": {
            "BatteryManagement": {"WinterModeOperation": 1},
        },
    }
    api._update_vehicle_properties_ccs2(vehicle, state)

    assert vehicle.ev_off_peak_start_time is None
    assert vehicle.ev_off_peak_end_time is None
    assert vehicle.ev_schedule_charge_enabled is None
    assert vehicle.ev_off_peak_charge_only_enabled is None
    assert vehicle.ev_battery_precondition_enabled is True
    assert vehicle.ev_battery_winter_mode is True


def test_ccs2_parser_ev6_offpeak_mode_zero(api: KiaCciApiEU, vehicle: Vehicle) -> None:
    """The live EV6 GSPA stored-status fixture carries OffPeakTime Mode=0:
    window parsed, schedule flag False, off-peak-only None."""
    import datetime as dt

    data = load_fixture(FIXTURE)
    api._update_vehicle_properties_ccs2(vehicle, data)

    assert vehicle.ev_off_peak_start_time == dt.time(22, 0)
    assert vehicle.ev_off_peak_end_time == dt.time(1, 30)
    assert vehicle.ev_schedule_charge_enabled is False
    assert vehicle.ev_off_peak_charge_only_enabled is None
    # EV6 sends BatteryPreCondition.Status=0 → precondition off (was
    # wrongly True via object truthiness).
    assert vehicle.ev_battery_precondition_enabled is False
    assert vehicle.ev_battery_winter_mode is None
    assert vehicle.headlamp_status == 0
