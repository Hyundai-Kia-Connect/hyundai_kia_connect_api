"""Tests for EU API _update_vehicle_properties using JSON fixture files.

Tests KiaUvoApiEU._update_vehicle_properties with fixtures that use the
``vehicleStatus.*`` path structure with hex-encoded temperatures.
"""

import pytest

from hyundai_kia_connect_api.KiaUvoApiEU import KiaUvoApiEU
from hyundai_kia_connect_api.Vehicle import Vehicle

from tests.fixture_helpers import (
    discover_fixtures,
    get_fixture_expected,
    get_fixture_meta,
    load_fixture,
)

EU_FIXTURE_FILES = discover_fixtures("eu_kia_ev6_")


@pytest.fixture
def eu_api() -> KiaUvoApiEU:
    api = KiaUvoApiEU.__new__(KiaUvoApiEU)
    api.data_timezone = KiaUvoApiEU.data_timezone
    api.temperature_range = KiaUvoApiEU.temperature_range
    return api


@pytest.fixture
def vehicle() -> Vehicle:
    return Vehicle()


@pytest.mark.parametrize("fixture_file", EU_FIXTURE_FILES, ids=EU_FIXTURE_FILES)
class TestEUUpdateVehicleProperties:
    def test_ev_battery_percentage(self, eu_api, vehicle, fixture_file):
        data = load_fixture(fixture_file)
        expected = get_fixture_expected(data)
        eu_api._update_vehicle_properties(vehicle, data)
        assert vehicle.ev_battery_percentage == expected["ev_battery_percentage"]

    def test_ev_charge_limits(self, eu_api, vehicle, fixture_file):
        data = load_fixture(fixture_file)
        meta = get_fixture_meta(data)
        expected = get_fixture_expected(data)
        eu_api._update_vehicle_properties(vehicle, data)

        if meta.get("has_target_soc"):
            assert vehicle.ev_charge_limits_dc == expected["ev_charge_limits_dc"]
            assert vehicle.ev_charge_limits_ac == expected["ev_charge_limits_ac"]
        else:
            assert vehicle.ev_charge_limits_dc is None
            assert vehicle.ev_charge_limits_ac is None

    def test_car_battery_percentage(self, eu_api, vehicle, fixture_file):
        data = load_fixture(fixture_file)
        expected = get_fixture_expected(data)
        eu_api._update_vehicle_properties(vehicle, data)
        assert vehicle.car_battery_percentage == expected["car_battery_percentage"]

    def test_engine_is_running(self, eu_api, vehicle, fixture_file):
        data = load_fixture(fixture_file)
        expected = get_fixture_expected(data)
        eu_api._update_vehicle_properties(vehicle, data)
        assert vehicle.engine_is_running == expected["engine_is_running"]

    def test_is_locked(self, eu_api, vehicle, fixture_file):
        data = load_fixture(fixture_file)
        expected = get_fixture_expected(data)
        eu_api._update_vehicle_properties(vehicle, data)
        assert vehicle.is_locked == expected["is_locked"]

    def test_ev_charging_state(self, eu_api, vehicle, fixture_file):
        data = load_fixture(fixture_file)
        expected = get_fixture_expected(data)
        eu_api._update_vehicle_properties(vehicle, data)
        assert vehicle.ev_battery_is_charging == expected["ev_battery_is_charging"]
        assert vehicle.ev_battery_is_plugged_in == expected["ev_battery_is_plugged_in"]

    def test_data_is_stored(self, eu_api, vehicle, fixture_file):
        data = load_fixture(fixture_file)
        eu_api._update_vehicle_properties(vehicle, data)
        assert vehicle.data is data
