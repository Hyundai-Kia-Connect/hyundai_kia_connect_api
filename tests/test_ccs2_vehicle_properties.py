"""Tests for CCS2 protocol _update_vehicle_properties_ccs2 using JSON fixtures.

Tests ApiImplType1._update_vehicle_properties_ccs2 with fixtures that use
the newer CCS2 protocol structure (Drivetrain/Cabin/Green/Body/Chassis paths).
TargetSoC in CCS2 is direct scalar values, not arrays.
"""

import pytest

from hyundai_kia_connect_api.ApiImplType1 import ApiImplType1
from hyundai_kia_connect_api.Vehicle import Vehicle
from hyundai_kia_connect_api.const import PressureUnit

from tests.fixture_helpers import (
    discover_fixtures,
    get_fixture_expected,
    get_fixture_meta,
    load_fixture,
)

CCS2_FIXTURE_FILES = discover_fixtures("eu_kia_ev9_")


@pytest.fixture
def ccs2_api() -> ApiImplType1:
    api = ApiImplType1.__new__(ApiImplType1)
    api.data_timezone = None
    api.temperature_range = [x * 0.5 for x in range(28, 60)]
    return api


@pytest.fixture
def vehicle() -> Vehicle:
    return Vehicle()


@pytest.mark.parametrize("fixture_file", CCS2_FIXTURE_FILES, ids=CCS2_FIXTURE_FILES)
class TestCCS2UpdateVehicleProperties:
    def test_ev_battery_percentage(self, ccs2_api, vehicle, fixture_file):
        data = load_fixture(fixture_file)
        expected = get_fixture_expected(data)
        ccs2_api._update_vehicle_properties_ccs2(vehicle, data)
        assert vehicle.ev_battery_percentage == expected["ev_battery_percentage"]

    def test_ev_charge_limits(self, ccs2_api, vehicle, fixture_file):
        data = load_fixture(fixture_file)
        meta = get_fixture_meta(data)
        expected = get_fixture_expected(data)
        ccs2_api._update_vehicle_properties_ccs2(vehicle, data)

        if meta.get("has_target_soc"):
            assert vehicle.ev_charge_limits_dc == expected["ev_charge_limits_dc"]
            assert vehicle.ev_charge_limits_ac == expected["ev_charge_limits_ac"]

    def test_car_battery_percentage(self, ccs2_api, vehicle, fixture_file):
        data = load_fixture(fixture_file)
        expected = get_fixture_expected(data)
        ccs2_api._update_vehicle_properties_ccs2(vehicle, data)
        assert vehicle.car_battery_percentage == expected["car_battery_percentage"]

    def test_engine_is_running(self, ccs2_api, vehicle, fixture_file):
        data = load_fixture(fixture_file)
        expected = get_fixture_expected(data)
        ccs2_api._update_vehicle_properties_ccs2(vehicle, data)
        assert vehicle.engine_is_running == expected["engine_is_running"]

    def test_is_locked(self, ccs2_api, vehicle, fixture_file):
        data = load_fixture(fixture_file)
        expected = get_fixture_expected(data)
        ccs2_api._update_vehicle_properties_ccs2(vehicle, data)
        assert vehicle.is_locked == expected["is_locked"]

    def test_ev_plugged_in(self, ccs2_api, vehicle, fixture_file):
        data = load_fixture(fixture_file)
        expected = get_fixture_expected(data)
        ccs2_api._update_vehicle_properties_ccs2(vehicle, data)
        assert vehicle.ev_battery_is_plugged_in == expected["ev_battery_is_plugged_in"]

    def test_data_is_stored(self, ccs2_api, vehicle, fixture_file):
        data = load_fixture(fixture_file)
        ccs2_api._update_vehicle_properties_ccs2(vehicle, data)
        assert vehicle.data is data


def _window_state(open_front_left=0, open_level_front_left=0, **levels) -> dict:
    """Build a CCS2 state dict from the EV9 fixture with Window overridden.

    Reuses the real fixture so every non-window branch of
    ``_update_vehicle_properties_ccs2`` still has the fields it expects; only
    ``Cabin.Window`` is replaced with the given Open/OpenLevel values.
    """
    import copy

    data = copy.deepcopy(load_fixture(CCS2_FIXTURE_FILES[0]))
    row1 = {
        "Driver": {"Open": open_front_left, "OpenLevel": open_level_front_left},
        "Passenger": {
            "Open": levels.get("open_front_right", 0),
            "OpenLevel": levels.get("open_level_front_right", 0),
        },
    }
    row2 = {
        "Left": {
            "Open": levels.get("open_back_left", 0),
            "OpenLevel": levels.get("open_level_back_left", 0),
        },
        "Right": {
            "Open": levels.get("open_back_right", 0),
            "OpenLevel": levels.get("open_level_back_right", 0),
        },
    }
    data["Cabin"]["Window"] = {"Row1": row1, "Row2": row2}
    return data


class TestCCS2WindowOpenLevel:
    """CCS2 vents report Open=0, OpenLevel>0 and must parse as open (#1215)."""

    def test_closed_windows_are_false(self, ccs2_api, vehicle):
        ccs2_api._update_vehicle_properties_ccs2(vehicle, _window_state())
        assert vehicle.front_left_window_is_open is False
        assert vehicle.front_right_window_is_open is False
        assert vehicle.back_left_window_is_open is False
        assert vehicle.back_right_window_is_open is False

    def test_vented_window_is_open(self, ccs2_api, vehicle):
        # Vent: Open=0, OpenLevel=1 (issue #1215 reproduction)
        ccs2_api._update_vehicle_properties_ccs2(
            vehicle, _window_state(open_level_front_left=1, open_level_front_right=1)
        )
        assert vehicle.front_left_window_is_open is True
        assert vehicle.front_right_window_is_open is True
        assert vehicle.back_left_window_is_open is False
        assert vehicle.back_right_window_is_open is False

    def test_fully_open_window_is_open(self, ccs2_api, vehicle):
        ccs2_api._update_vehicle_properties_ccs2(
            vehicle, _window_state(open_front_left=1, open_back_right=1)
        )
        assert vehicle.front_left_window_is_open is True
        assert vehicle.back_right_window_is_open is True

    def test_missing_window_fields_are_none(self, ccs2_api, vehicle):
        data = load_fixture(CCS2_FIXTURE_FILES[0])
        del data["Cabin"]["Window"]
        ccs2_api._update_vehicle_properties_ccs2(vehicle, data)
        assert vehicle.front_left_window_is_open is None
        assert vehicle.back_right_window_is_open is None


@pytest.fixture
def ccs2_state_new_fields():
    """A complete, parser-safe CCS2 state (EU EV9 2024 fixture) with the new
    Tier 1 fields overlaid: tire Pressure values + PressureUnit, DrivingMode,
    OilLevelWarning, Auxiliary.FailWarning. load_fixture returns a fresh dict
    per call, so per-test mutation (e.g. popping Pressure) is safe."""
    state = load_fixture("eu_kia_ev9_2024_ccs2.json")
    axle = state["Chassis"]["Axle"]
    axle["Row1"]["Left"]["Tire"]["Pressure"] = 27
    axle["Row1"]["Right"]["Tire"]["Pressure"] = 27
    axle["Row2"]["Left"]["Tire"]["Pressure"] = 27
    axle["Row2"]["Right"]["Tire"]["Pressure"] = 26
    axle["Tire"]["PressureUnit"] = 2
    state.setdefault("Chassis", {}).setdefault("DrivingMode", {})["State"] = "Eco"
    state.setdefault("Drivetrain", {}).setdefault("InternalCombustionEngine", {})[
        "OilLevelWarning"
    ] = 0
    state.setdefault("Electronics", {}).setdefault("Battery", {}).setdefault(
        "Auxiliary", {}
    )["FailWarning"] = 0
    return state


def test_tire_pressure_values_bar(ccs2_api, vehicle, ccs2_state_new_fields):
    # Confirmed live (EU Santa Fe 2026, car display unit = bar, PressureUnit=2):
    # raw 27/27/27/26 -> 2.7/2.7/2.7/2.6 bar (raw x 0.1).
    ccs2_api._update_vehicle_properties_ccs2(vehicle, ccs2_state_new_fields)
    assert vehicle.tire_pressure_front_left == 2.7
    assert vehicle.tire_pressure_front_right == 2.7
    assert vehicle.tire_pressure_rear_left == 2.7
    assert vehicle.tire_pressure_rear_right == 2.6
    assert vehicle.tire_pressure_unit == PressureUnit.BAR
    assert vehicle.tire_pressure_front_left_unit == "bar"
    assert vehicle.tire_pressure_rear_right_unit == "bar"


def test_tire_pressure_values_psi(ccs2_api, vehicle, ccs2_state_new_fields):
    # Confirmed live (car display unit = psi, PressureUnit=0): raw 38/38/37/36
    # -> 38/38/37/36 psi (raw x 1, integer psi). Model B (raw unit-dependent).
    axle = ccs2_state_new_fields["Chassis"]["Axle"]
    axle["Tire"]["PressureUnit"] = 0
    axle["Row1"]["Left"]["Tire"]["Pressure"] = 38
    axle["Row1"]["Right"]["Tire"]["Pressure"] = 38
    axle["Row2"]["Left"]["Tire"]["Pressure"] = 37
    axle["Row2"]["Right"]["Tire"]["Pressure"] = 36
    ccs2_api._update_vehicle_properties_ccs2(vehicle, ccs2_state_new_fields)
    assert vehicle.tire_pressure_front_left == 38.0
    assert vehicle.tire_pressure_front_right == 38.0
    assert vehicle.tire_pressure_rear_left == 37.0
    assert vehicle.tire_pressure_rear_right == 36.0
    assert vehicle.tire_pressure_unit == PressureUnit.PSI
    assert vehicle.tire_pressure_front_left_unit == "psi"


def test_tire_pressure_values_kpa(ccs2_api, vehicle, ccs2_state_new_fields):
    # Live-confirmed 2026-07-02: PressureUnit=1 (kPa), raw in 5-kPa steps.
    # Dashboard 255/255/255/250 kPa -> raw 51/51/51/50 -> value = raw x 5 = kPa.
    axle = ccs2_state_new_fields["Chassis"]["Axle"]
    axle["Tire"]["PressureUnit"] = 1
    axle["Row1"]["Left"]["Tire"]["Pressure"] = 51
    axle["Row1"]["Right"]["Tire"]["Pressure"] = 51
    axle["Row2"]["Left"]["Tire"]["Pressure"] = 51
    axle["Row2"]["Right"]["Tire"]["Pressure"] = 50
    ccs2_api._update_vehicle_properties_ccs2(vehicle, ccs2_state_new_fields)
    assert vehicle.tire_pressure_front_left == 255.0
    assert vehicle.tire_pressure_rear_right == 250.0
    assert vehicle.tire_pressure_unit == PressureUnit.KPA
    assert vehicle.tire_pressure_front_left_unit == "kPa"


def test_tire_pressure_missing_leaves_none(ccs2_api, vehicle, ccs2_state_new_fields):
    # Older CCS2 responses (e.g. EU EV9 2024) report PressureLow but no Pressure
    # and no PressureUnit -> values + units stay None (entity not created).
    for row in ("Row1", "Row2"):
        for side in ("Left", "Right"):
            ccs2_state_new_fields["Chassis"]["Axle"][row][side]["Tire"].pop("Pressure")
    ccs2_state_new_fields["Chassis"]["Axle"]["Tire"].pop("PressureUnit")
    ccs2_api._update_vehicle_properties_ccs2(vehicle, ccs2_state_new_fields)
    assert vehicle.tire_pressure_front_left is None
    assert vehicle.tire_pressure_front_right is None
    assert vehicle.tire_pressure_rear_left is None
    assert vehicle.tire_pressure_rear_right is None
    assert vehicle.tire_pressure_unit is None
    assert vehicle.tire_pressure_front_left_unit is None


def test_drive_mode(ccs2_api, vehicle, ccs2_state_new_fields):
    ccs2_api._update_vehicle_properties_ccs2(vehicle, ccs2_state_new_fields)
    assert vehicle.drive_mode == "Eco"


def test_oil_level_warning_false(ccs2_api, vehicle, ccs2_state_new_fields):
    ccs2_api._update_vehicle_properties_ccs2(vehicle, ccs2_state_new_fields)
    assert vehicle.oil_level_warning_is_on is False


def test_oil_level_warning_missing_leaves_none(
    ccs2_api, vehicle, ccs2_state_new_fields
):
    # No OilLevelWarning -> attribute stays None (entity not created downstream).
    del ccs2_state_new_fields["Drivetrain"]["InternalCombustionEngine"][
        "OilLevelWarning"
    ]
    ccs2_api._update_vehicle_properties_ccs2(vehicle, ccs2_state_new_fields)
    assert vehicle.oil_level_warning_is_on is None


def test_battery_auxiliary_fail_warning_false(ccs2_api, vehicle, ccs2_state_new_fields):
    ccs2_api._update_vehicle_properties_ccs2(vehicle, ccs2_state_new_fields)
    assert vehicle.battery_auxiliary_fail_warning_is_on is False


def test_battery_auxiliary_fail_warning_missing_leaves_none(
    ccs2_api, vehicle, ccs2_state_new_fields
):
    # No FailWarning -> attribute stays None (entity not created downstream).
    del ccs2_state_new_fields["Electronics"]["Battery"]["Auxiliary"]["FailWarning"]
    ccs2_api._update_vehicle_properties_ccs2(vehicle, ccs2_state_new_fields)
    assert vehicle.battery_auxiliary_fail_warning_is_on is None
