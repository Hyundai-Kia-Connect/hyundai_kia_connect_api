"""Regression tests for KiaUvoApiUSA._update_vehicle_properties.

Fixture-driven parsing is covered by tests/test_fixture_parsing.py; this
module holds targeted cases built from minimal inline payloads.
"""

import pytest

from hyundai_kia_connect_api.KiaUvoApiUSA import KiaUvoApiUSA
from hyundai_kia_connect_api.Vehicle import Vehicle
from tests.fixture_helpers import PARSERS


@pytest.fixture
def usa_api() -> KiaUvoApiUSA:
    return PARSERS["usa_kia"].api()


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


def test_usa_air_temperature_string_becomes_float(usa_api, us_kia_status_with_air_temp):
    """Regression for kia_uvo #1755: USA backend must not leave air_temperature
    as a string. The Vehicle setter coerces to float; verify end-to-end."""
    vehicle = Vehicle()
    usa_api._update_vehicle_properties(vehicle, us_kia_status_with_air_temp)
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


def test_usa_air_temperature_off_yields_none(usa_api, us_kia_status_with_air_temp_off):
    """Regression for kia_uvo #1790: Kia USA airTemp "OFF" (climate off) must
    leave air_temperature and the raw value slot as None, matching the Hyundai
    USA / Type1 / CA skip-OFF conformance. float_or_none("OFF") already yields
    None, so the real TDD gate is _air_temperature_value staying None (setter
    not called)."""
    vehicle = Vehicle()
    usa_api._update_vehicle_properties(vehicle, us_kia_status_with_air_temp_off)
    assert vehicle.air_temperature is None
    assert vehicle._air_temperature_value is None
