"""Tests for the battery state of health parsing."""

import pytest

from hyundai_kia_connect_api.ApiImplType1 import ApiImplType1
from hyundai_kia_connect_api.KiaUvoApiEU import KiaUvoApiEU
from hyundai_kia_connect_api.Vehicle import Vehicle
from tests.fixture_helpers import load_fixture

FIXTURE_FILE = "eu_kia_ev6_2023_with_soc.json"
CCS2_FIXTURE_FILE = "eu_kia_ev6_2024_ccs2_off.json"


@pytest.fixture
def eu_api() -> KiaUvoApiEU:
    api = KiaUvoApiEU.__new__(KiaUvoApiEU)
    api.data_timezone = KiaUvoApiEU.data_timezone
    api.temperature_range = KiaUvoApiEU.temperature_range
    return api


@pytest.fixture
def vehicle() -> Vehicle:
    return Vehicle()


def test_battery_soh_is_read(eu_api, vehicle):
    """A reported state of health ends up on the vehicle."""
    data = load_fixture(FIXTURE_FILE)
    data["vehicleStatus"]["evStatus"]["batterySoh"] = 88

    eu_api._update_vehicle_properties(vehicle, data)

    assert vehicle.ev_battery_soh_percentage == 88


def test_battery_soh_zero_is_unknown(eu_api, vehicle):
    """A vehicle reporting 0 leaves the state of health unknown."""
    data = load_fixture(FIXTURE_FILE)
    data["vehicleStatus"]["evStatus"]["batterySoh"] = 0

    eu_api._update_vehicle_properties(vehicle, data)

    assert vehicle.ev_battery_soh_percentage is None


@pytest.fixture
def ccs2_api() -> ApiImplType1:
    api = ApiImplType1.__new__(ApiImplType1)
    api.data_timezone = None
    api.temperature_range = [x * 0.5 for x in range(28, 60)]
    return api


def test_ccs2_battery_soh_is_read(ccs2_api, vehicle):
    """A reported state of health ends up on the vehicle."""
    data = load_fixture(CCS2_FIXTURE_FILE)
    data["Green"]["BatteryManagement"]["SoH"]["Ratio"] = 95

    ccs2_api._update_vehicle_properties_ccs2(vehicle, data)

    assert vehicle.ev_battery_soh_percentage == 95


def test_ccs2_battery_soh_zero_is_unknown(ccs2_api, vehicle):
    """A vehicle reporting 0 leaves the state of health unknown."""
    data = load_fixture(CCS2_FIXTURE_FILE)
    data["Green"]["BatteryManagement"]["SoH"]["Ratio"] = 0

    ccs2_api._update_vehicle_properties_ccs2(vehicle, data)

    assert vehicle.ev_battery_soh_percentage is None
