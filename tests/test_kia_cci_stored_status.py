"""Tests for KiaCciApiEU cached-state edge cases and CCS2 parser paths.

Parsing of the live Kia GSPA stored-status fixtures (EV6 2024, PV5 2026) is
covered by tests/test_vehicle_update_snapshots.py; these tests cover token
and empty-response handling and parser paths the fixtures don't carry."""

from unittest.mock import patch

import pytest

from hyundai_kia_connect_api.KiaCciApiEU import KiaCciApiEU
from hyundai_kia_connect_api.Token import Token
from hyundai_kia_connect_api.Vehicle import Vehicle


@pytest.fixture
def api() -> KiaCciApiEU:
    return KiaCciApiEU.__new__(KiaCciApiEU)


@pytest.fixture
def vehicle() -> Vehicle:
    return Vehicle()


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


def test_ccs2_parser_expected_time_in_range(api: KiaCciApiEU, vehicle: Vehicle) -> None:
    """A configured ExpectedTime (Hour/Min in range) parses to times; the
    live payloads only ever carry the unconfigured sentinel."""
    import datetime as dt

    state = {
        "Green": {
            "ChargingInformation": {
                "ExpectedTime": {
                    "StartDay": 1,
                    "StartHour": 22,
                    "StartMin": 5,
                    "EndDay": 1,
                    "EndHour": 6,
                    "EndMin": 30,
                }
            }
        }
    }
    api._update_vehicle_properties_ccs2(vehicle, state)

    assert vehicle.ev_charge_expected_start_time == dt.time(22, 5)
    assert vehicle.ev_charge_expected_end_time == dt.time(6, 30)
