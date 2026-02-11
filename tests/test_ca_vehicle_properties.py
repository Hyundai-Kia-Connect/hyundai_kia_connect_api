"""Tests for CA API _update_vehicle_properties_base using JSON fixture files.

Tests KiaUvoApiCA._update_vehicle_properties_base with fixtures that use the
``status.*`` path structure. Note: charge limits in CA are handled by a
separate ``_update_vehicle_properties_charge`` call with data from ``evc/selsoc``.
"""

import pytest

from hyundai_kia_connect_api.KiaUvoApiCA import KiaUvoApiCA
from hyundai_kia_connect_api.Vehicle import Vehicle

from tests.fixture_helpers import discover_fixtures, get_fixture_expected, load_fixture

CA_FIXTURE_FILES = discover_fixtures("ca_")


@pytest.fixture
def ca_api() -> KiaUvoApiCA:
    api = KiaUvoApiCA.__new__(KiaUvoApiCA)
    api.data_timezone = KiaUvoApiCA.data_timezone
    api.temperature_range_c_old = KiaUvoApiCA.temperature_range_c_old
    api.temperature_range_c_new = KiaUvoApiCA.temperature_range_c_new
    api.temperature_range_model_year = KiaUvoApiCA.temperature_range_model_year
    return api


@pytest.fixture
def vehicle() -> Vehicle:
    v = Vehicle()
    v.year = 2022
    return v


@pytest.mark.parametrize("fixture_file", CA_FIXTURE_FILES, ids=CA_FIXTURE_FILES)
class TestCAUpdateVehicleProperties:
    def test_ev_battery_percentage(self, ca_api, vehicle, fixture_file):
        data = load_fixture(fixture_file)
        expected = get_fixture_expected(data)
        ca_api._update_vehicle_properties_base(vehicle, data)
        assert vehicle.ev_battery_percentage == expected["ev_battery_percentage"]

    def test_car_battery_percentage(self, ca_api, vehicle, fixture_file):
        data = load_fixture(fixture_file)
        expected = get_fixture_expected(data)
        ca_api._update_vehicle_properties_base(vehicle, data)
        assert vehicle.car_battery_percentage == expected["car_battery_percentage"]

    def test_engine_is_running(self, ca_api, vehicle, fixture_file):
        data = load_fixture(fixture_file)
        expected = get_fixture_expected(data)
        ca_api._update_vehicle_properties_base(vehicle, data)
        assert vehicle.engine_is_running == expected["engine_is_running"]

    def test_is_locked(self, ca_api, vehicle, fixture_file):
        data = load_fixture(fixture_file)
        expected = get_fixture_expected(data)
        ca_api._update_vehicle_properties_base(vehicle, data)
        assert vehicle.is_locked == expected["is_locked"]

    def test_ev_charging_state(self, ca_api, vehicle, fixture_file):
        data = load_fixture(fixture_file)
        expected = get_fixture_expected(data)
        ca_api._update_vehicle_properties_base(vehicle, data)
        assert vehicle.ev_battery_is_charging == expected["ev_battery_is_charging"]
        assert vehicle.ev_battery_is_plugged_in == expected["ev_battery_is_plugged_in"]

    def test_charge_limits_not_in_base(self, ca_api, vehicle, fixture_file):
        """CA charge limits come from a separate API call, not the base status."""
        data = load_fixture(fixture_file)
        ca_api._update_vehicle_properties_base(vehicle, data)
        assert vehicle.ev_charge_limits_dc is None
        assert vehicle.ev_charge_limits_ac is None

    def test_data_is_stored(self, ca_api, vehicle, fixture_file):
        """CA stores status sub-dict in vehicle.data['status'], not the full state."""
        data = load_fixture(fixture_file)
        ca_api._update_vehicle_properties_base(vehicle, data)
        assert vehicle.data["status"] is data["status"]
