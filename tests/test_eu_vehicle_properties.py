"""Tests for EU API _update_vehicle_properties using JSON fixture files.

Tests KiaUvoApiEU._update_vehicle_properties with fixtures that use the
``vehicleStatus.*`` path structure with hex-encoded temperatures.
"""

import pytest

from hyundai_kia_connect_api.KiaUvoApiEU import KiaUvoApiEU
from hyundai_kia_connect_api.Vehicle import Vehicle
from tests.fixture_helpers import load_fixture


@pytest.fixture
def eu_api() -> KiaUvoApiEU:
    api = KiaUvoApiEU.__new__(KiaUvoApiEU)
    api.data_timezone = KiaUvoApiEU.data_timezone
    api.temperature_range = KiaUvoApiEU.temperature_range
    return api


@pytest.fixture
def vehicle() -> Vehicle:
    return Vehicle()


def test_ev_timers_unset_when_api_returns_zero(eu_api, vehicle):
    """issue #1206: '0000' means no scheduled timer -> None, no crash."""
    data = load_fixture("eu_kia_ev6_unset_timers.json")
    eu_api._update_vehicle_properties(vehicle, data)
    assert vehicle.ev_first_departure_time is None
    assert vehicle.ev_second_departure_time is None
    assert vehicle.ev_off_peak_start_time is None
    assert vehicle.ev_off_peak_end_time is None
