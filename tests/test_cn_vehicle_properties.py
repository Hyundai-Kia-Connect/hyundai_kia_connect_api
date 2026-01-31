"""Tests for CN API _update_vehicle_properties using JSON fixture files.

Tests KiaUvoApiCN._update_vehicle_properties with fixtures that use the
``status.*`` path structure (mirrors AU and EU).
"""

import pytest

from hyundai_kia_connect_api.KiaUvoApiCN import KiaUvoApiCN
from hyundai_kia_connect_api.Vehicle import Vehicle

from tests.fixture_helpers import (
    discover_fixtures,
    get_fixture_expected,
    get_fixture_meta,
    load_fixture,
)

CN_FIXTURE_FILES = discover_fixtures("cn_")


@pytest.fixture
def cn_api() -> KiaUvoApiCN:
    api = KiaUvoApiCN.__new__(KiaUvoApiCN)
    api.data_timezone = KiaUvoApiCN.data_timezone
    api.temperature_range = KiaUvoApiCN.temperature_range
    return api


@pytest.fixture
def vehicle() -> Vehicle:
    return Vehicle()


@pytest.mark.parametrize("fixture_file", CN_FIXTURE_FILES, ids=CN_FIXTURE_FILES)
class TestCNUpdateVehicleProperties:
    def test_ev_battery_percentage(self, cn_api, vehicle, fixture_file):
        data = load_fixture(fixture_file)
        expected = get_fixture_expected(data)
        cn_api._update_vehicle_properties(vehicle, data)
        assert vehicle.ev_battery_percentage == expected["ev_battery_percentage"]

    def test_ev_charge_limits(self, cn_api, vehicle, fixture_file):
        data = load_fixture(fixture_file)
        meta = get_fixture_meta(data)
        expected = get_fixture_expected(data)
        cn_api._update_vehicle_properties(vehicle, data)

        if meta.get("has_target_soc"):
            assert vehicle.ev_charge_limits_dc == expected["ev_charge_limits_dc"]
            assert vehicle.ev_charge_limits_ac == expected["ev_charge_limits_ac"]
        else:
            assert vehicle.ev_charge_limits_dc is None
            assert vehicle.ev_charge_limits_ac is None

    def test_car_battery_percentage(self, cn_api, vehicle, fixture_file):
        data = load_fixture(fixture_file)
        expected = get_fixture_expected(data)
        cn_api._update_vehicle_properties(vehicle, data)
        assert vehicle.car_battery_percentage == expected["car_battery_percentage"]

    def test_engine_is_running(self, cn_api, vehicle, fixture_file):
        data = load_fixture(fixture_file)
        expected = get_fixture_expected(data)
        cn_api._update_vehicle_properties(vehicle, data)
        assert vehicle.engine_is_running == expected["engine_is_running"]

    def test_is_locked(self, cn_api, vehicle, fixture_file):
        data = load_fixture(fixture_file)
        expected = get_fixture_expected(data)
        cn_api._update_vehicle_properties(vehicle, data)
        assert vehicle.is_locked == expected["is_locked"]

    def test_ev_charging_state(self, cn_api, vehicle, fixture_file):
        data = load_fixture(fixture_file)
        expected = get_fixture_expected(data)
        cn_api._update_vehicle_properties(vehicle, data)
        assert vehicle.ev_battery_is_charging == expected["ev_battery_is_charging"]
        assert vehicle.ev_battery_is_plugged_in == expected["ev_battery_is_plugged_in"]

    def test_data_is_stored(self, cn_api, vehicle, fixture_file):
        data = load_fixture(fixture_file)
        cn_api._update_vehicle_properties(vehicle, data)
        assert vehicle.data is data
