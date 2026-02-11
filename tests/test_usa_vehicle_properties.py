"""Tests for USA API _update_vehicle_properties using JSON fixture files.

These tests verify that the KiaUvoApiUSA._update_vehicle_properties method
correctly parses vehicle state dicts (as returned by cmm/gvi) into Vehicle
objects. Each fixture file represents a real-world API response shape for a
specific vehicle model and scenario.

To add a new vehicle/scenario:
  1. Drop a JSON file in tests/fixtures/ following the naming convention
     (see tests/fixtures/README.md).
  2. Include a ``_fixture_meta.expected`` block with the values to assert.
  3. The test will automatically pick it up via ``discover_fixtures``.
"""

import pytest

from hyundai_kia_connect_api.KiaUvoApiUSA import KiaUvoApiUSA
from hyundai_kia_connect_api.Vehicle import Vehicle

from tests.fixture_helpers import (
    discover_fixtures,
    get_fixture_expected,
    get_fixture_meta,
    load_fixture,
)

# ---------------------------------------------------------------------------
# Discover all US fixture files (both cached and force-refresh variants)
# ---------------------------------------------------------------------------
US_FIXTURE_FILES = discover_fixtures("us_kia_")


@pytest.fixture
def usa_api() -> KiaUvoApiUSA:
    """Create a KiaUvoApiUSA instance for testing property parsing."""
    api = KiaUvoApiUSA.__new__(KiaUvoApiUSA)
    api.data_timezone = None
    # temperature_range is used when air_temp is "LOW" or "HIGH"
    api.temperature_range = [62, 64, 66, 68, 70, 72, 74, 76, 78, 80, 82]
    return api


@pytest.fixture
def vehicle() -> Vehicle:
    """Create a blank Vehicle instance."""
    return Vehicle()


# ---------------------------------------------------------------------------
# Parameterized tests — each fixture file is a separate test case
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("fixture_file", US_FIXTURE_FILES, ids=US_FIXTURE_FILES)
class TestUpdateVehicleProperties:
    """Parameterized suite that runs against every US fixture file."""

    def test_ev_battery_percentage(self, usa_api, vehicle, fixture_file):
        data = load_fixture(fixture_file)
        expected = get_fixture_expected(data)
        usa_api._update_vehicle_properties(vehicle, data)
        assert vehicle.ev_battery_percentage == expected["ev_battery_percentage"]

    def test_ev_charge_limits(self, usa_api, vehicle, fixture_file):
        data = load_fixture(fixture_file)
        meta = get_fixture_meta(data)
        expected = get_fixture_expected(data)
        usa_api._update_vehicle_properties(vehicle, data)

        if meta.get("has_target_soc"):
            assert vehicle.ev_charge_limits_dc == expected["ev_charge_limits_dc"]
            assert vehicle.ev_charge_limits_ac == expected["ev_charge_limits_ac"]
        else:
            # When targetSOC is absent, charge limits should remain None
            assert vehicle.ev_charge_limits_dc is None
            assert vehicle.ev_charge_limits_ac is None

    def test_car_battery_percentage(self, usa_api, vehicle, fixture_file):
        data = load_fixture(fixture_file)
        expected = get_fixture_expected(data)
        usa_api._update_vehicle_properties(vehicle, data)
        assert vehicle.car_battery_percentage == expected["car_battery_percentage"]

    def test_engine_is_running(self, usa_api, vehicle, fixture_file):
        data = load_fixture(fixture_file)
        expected = get_fixture_expected(data)
        usa_api._update_vehicle_properties(vehicle, data)
        assert vehicle.engine_is_running == expected["engine_is_running"]

    def test_is_locked(self, usa_api, vehicle, fixture_file):
        data = load_fixture(fixture_file)
        expected = get_fixture_expected(data)
        usa_api._update_vehicle_properties(vehicle, data)
        assert vehicle.is_locked == expected["is_locked"]

    def test_ev_charging_state(self, usa_api, vehicle, fixture_file):
        data = load_fixture(fixture_file)
        expected = get_fixture_expected(data)
        usa_api._update_vehicle_properties(vehicle, data)
        assert vehicle.ev_battery_is_charging == expected["ev_battery_is_charging"]
        assert vehicle.ev_battery_is_plugged_in == expected["ev_battery_is_plugged_in"]

    def test_data_is_stored(self, usa_api, vehicle, fixture_file):
        """The raw state dict should be stored on vehicle.data."""
        data = load_fixture(fixture_file)
        usa_api._update_vehicle_properties(vehicle, data)
        assert vehicle.data is data
