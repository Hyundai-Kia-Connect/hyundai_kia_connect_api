"""Tests for USA API _update_vehicle_properties using JSON fixture files.

These tests verify that the KiaUvoApiUSA._update_vehicle_properties method
correctly parses vehicle state dicts (as returned by cmm/gvi) into Vehicle
objects. Fixture-wide parsing is covered by test_vehicle_update_snapshots.py.
"""

import pytest

from hyundai_kia_connect_api.KiaUvoApiUSA import KiaUvoApiUSA
from hyundai_kia_connect_api.Vehicle import Vehicle

# ---------------------------------------------------------------------------
# Regression: kia_uvo #1755 — USA backend must yield a numeric air_temperature
# ---------------------------------------------------------------------------


@pytest.fixture
def us_kia_status_with_air_temp():
    """Minimal USA cached-status payload with a string airTemp.value."""
    return {
        "lastVehicleInfo": {
            "vehicleStatusRpt": {
                "vehicleStatus": {
                    "climate": {
                        "airTemp": {"value": "72"},
                    },
                },
            },
        },
    }


def test_usa_air_temperature_string_becomes_float(us_kia_status_with_air_temp):
    """Regression for kia_uvo #1755: USA backend must not leave air_temperature
    as a string. The Vehicle setter coerces to float; verify end-to-end."""
    api = KiaUvoApiUSA.__new__(KiaUvoApiUSA)
    api.data_timezone = None
    api.temperature_range = [62, 64, 66, 68, 70, 72, 74, 76, 78, 80, 82]
    vehicle = Vehicle()
    api._update_vehicle_properties(vehicle, us_kia_status_with_air_temp)
    assert vehicle.air_temperature is not None
    assert isinstance(vehicle.air_temperature, float)
    assert vehicle.air_temperature == 72.0


# ---------------------------------------------------------------------------
# Regression: kia_uvo #1790 — Kia USA airTemp "OFF" (climate off) must not mask
# ---------------------------------------------------------------------------


@pytest.fixture
def us_kia_status_with_air_temp_off():
    """Minimal USA cached-status payload with airTemp.value == OFF."""
    return {
        "lastVehicleInfo": {
            "vehicleStatusRpt": {
                "vehicleStatus": {
                    "climate": {
                        "airTemp": {"value": "OFF"},
                    },
                },
            },
        },
    }


def test_usa_air_temperature_off_yields_none(us_kia_status_with_air_temp_off):
    """Regression for kia_uvo #1790: Kia USA airTemp "OFF" (climate off) must
    leave air_temperature and the raw value slot as None, matching the Hyundai
    USA / Type1 / CA skip-OFF conformance. float_or_none("OFF") already yields
    None, so the real TDD gate is _air_temperature_value staying None (setter
    not called)."""
    api = KiaUvoApiUSA.__new__(KiaUvoApiUSA)
    api.data_timezone = None
    api.temperature_range = [62, 64, 66, 68, 70, 72, 74, 76, 78, 80, 82]
    vehicle = Vehicle()
    api._update_vehicle_properties(vehicle, us_kia_status_with_air_temp_off)
    assert vehicle.air_temperature is None
    assert vehicle._air_temperature_value is None
