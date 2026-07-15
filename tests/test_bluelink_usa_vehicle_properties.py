"""Tests for Hyundai USA BlueLinkAPI _update_vehicle_properties using JSON fixtures.

Tests HyundaiBlueLinkApiUSA._update_vehicle_properties with fixtures that use
the ``vehicleStatus.*`` path structure (different from KiaUvoApiUSA which uses
``lastVehicleInfo.vehicleStatusRpt.vehicleStatus.*``).
"""

import pytest

from hyundai_kia_connect_api.HyundaiBlueLinkApiUSA import HyundaiBlueLinkApiUSA
from hyundai_kia_connect_api.Vehicle import Vehicle

from tests.fixture_helpers import (
    discover_fixtures,
    get_fixture_expected,
    get_fixture_meta,
    load_fixture,
)

BLUELINK_FIXTURE_FILES = discover_fixtures("us_hyundai_")


@pytest.fixture
def bluelink_api() -> HyundaiBlueLinkApiUSA:
    api = HyundaiBlueLinkApiUSA.__new__(HyundaiBlueLinkApiUSA)
    api.data_timezone = None
    api.temperature_range = range(62, 82)
    return api


@pytest.fixture
def vehicle() -> Vehicle:
    return Vehicle()


@pytest.mark.parametrize(
    "fixture_file", BLUELINK_FIXTURE_FILES, ids=BLUELINK_FIXTURE_FILES
)
class TestBlueLinkUSAUpdateVehicleProperties:
    def test_ev_battery_percentage(self, bluelink_api, vehicle, fixture_file):
        data = load_fixture(fixture_file)
        expected = get_fixture_expected(data)
        bluelink_api._update_vehicle_properties(vehicle, data)
        assert vehicle.ev_battery_percentage == expected["ev_battery_percentage"]

    def test_ev_charge_limits(self, bluelink_api, vehicle, fixture_file):
        data = load_fixture(fixture_file)
        meta = get_fixture_meta(data)
        expected = get_fixture_expected(data)
        bluelink_api._update_vehicle_properties(vehicle, data)

        if meta.get("has_target_soc"):
            assert vehicle.ev_charge_limits_dc == expected["ev_charge_limits_dc"]
            assert vehicle.ev_charge_limits_ac == expected["ev_charge_limits_ac"]
        else:
            assert vehicle.ev_charge_limits_dc is None
            assert vehicle.ev_charge_limits_ac is None

    def test_car_battery_percentage(self, bluelink_api, vehicle, fixture_file):
        data = load_fixture(fixture_file)
        expected = get_fixture_expected(data)
        bluelink_api._update_vehicle_properties(vehicle, data)
        assert vehicle.car_battery_percentage == expected["car_battery_percentage"]

    def test_engine_is_running(self, bluelink_api, vehicle, fixture_file):
        data = load_fixture(fixture_file)
        expected = get_fixture_expected(data)
        bluelink_api._update_vehicle_properties(vehicle, data)
        assert vehicle.engine_is_running == expected["engine_is_running"]

    def test_is_locked(self, bluelink_api, vehicle, fixture_file):
        data = load_fixture(fixture_file)
        expected = get_fixture_expected(data)
        bluelink_api._update_vehicle_properties(vehicle, data)
        assert vehicle.is_locked == expected["is_locked"]

    def test_ev_charging_state(self, bluelink_api, vehicle, fixture_file):
        data = load_fixture(fixture_file)
        expected = get_fixture_expected(data)
        bluelink_api._update_vehicle_properties(vehicle, data)
        assert vehicle.ev_battery_is_charging == expected["ev_battery_is_charging"]
        assert vehicle.ev_battery_is_plugged_in == expected["ev_battery_is_plugged_in"]

    def test_data_is_stored(self, bluelink_api, vehicle, fixture_file):
        data = load_fixture(fixture_file)
        bluelink_api._update_vehicle_properties(vehicle, data)
        assert vehicle.data is data


# ---------------------------------------------------------------------------
# Regression: kia_uvo #1790 — airTemp "OFF" (climate off) must not mask
# ---------------------------------------------------------------------------


@pytest.fixture
def bluelink_status_with_air_temp_off():
    """Minimal Hyundai USA cached-status payload with airTemp.value == OFF."""
    return {
        "vehicleStatus": {
            "airTemp": {"value": "OFF", "unit": 1, "hvacTempType": 1},
        },
    }


def test_bluelink_air_temp_off_yields_none(
    bluelink_api, bluelink_status_with_air_temp_off
):
    """Regression for kia_uvo #1790: when climate is off, the USA backend
    returns airTemp.value == "OFF". The setter must NOT be called with a
    non-numeric string; air_temperature and the raw value slot stay None so
    the kia_uvo sensor reports `unknown` (data unknown), not a fake setpoint.

    Note: float_or_none("OFF") already yields None, so asserting only on
    air_temperature would pass before the fix. The behavioral difference
    the conformance fixes is that the setter is not called at all — so the
    raw value slot (_air_temperature_value) stays None instead of holding
    the string "OFF". Assert on that side effect as the real TDD gate."""
    vehicle = Vehicle()
    bluelink_api._update_vehicle_properties(vehicle, bluelink_status_with_air_temp_off)
    assert vehicle.air_temperature is None
    assert vehicle._air_temperature_value is None
