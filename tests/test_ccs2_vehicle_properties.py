"""Tests for CCS2 protocol _update_vehicle_properties_ccs2 using JSON fixtures.

Tests ApiImplType1._update_vehicle_properties_ccs2 with fixtures that use
the newer CCS2 protocol structure (Drivetrain/Cabin/Green/Body/Chassis paths).
TargetSoC in CCS2 is direct scalar values, not arrays.
"""

import pytest

from hyundai_kia_connect_api.ApiImplType1 import ApiImplType1
from hyundai_kia_connect_api.Vehicle import Vehicle

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
