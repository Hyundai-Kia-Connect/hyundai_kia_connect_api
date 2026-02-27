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
