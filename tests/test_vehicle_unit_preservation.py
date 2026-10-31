"""Tests for Vehicle (value, unit) setter unit preservation.

When the API returns a value but no unit (unit is None), the setter must keep
the last known unit instead of overwriting it with None. Overwriting with None
causes Home Assistant unit flip-flop ("km" -> "" -> "km") and the recorder
error "unit (None) cannot be converted to previously compiled statistics (km)"
(see kia_uvo#1725, kia_uvo#1430).

The value itself is NOT preserved: if the API returns None for a value, that
is reflected (the data is genuinely unavailable). Only the unit is held, per
the maintainer's request in API#1161 ("only unit should hold").
"""

import pytest

from hyundai_kia_connect_api.Vehicle import Vehicle


@pytest.fixture
def vehicle():
    return Vehicle()


class TestUnitPreservation:
    """Each (value, unit) setter must preserve the last known unit on None."""

    @pytest.mark.parametrize(
        "attr",
        [
            "total_driving_range",
            "next_service_distance",
            "last_service_distance",
            "odometer",
            "outside_temperature",
            "air_temperature",
            "ev_battery_water_temperature",
            "ev_battery_temperature_min",
            "ev_battery_temperature_max",
            "ev_driving_range",
            "ev_estimated_current_charge_duration",
            "ev_estimated_fast_charge_duration",
            "ev_estimated_portable_charge_duration",
            "ev_estimated_station_charge_duration",
            "ev_target_range_charge_AC",
            "ev_target_range_charge_DC",
            "ev_first_departure_climate_temperature",
            "ev_second_departure_climate_temperature",
            "fuel_driving_range",
        ],
    )
    def test_unit_preserved_when_api_returns_none_unit(self, vehicle, attr):
        # First write sets both value and unit.
        setattr(vehicle, attr, (100, "km"))
        assert getattr(vehicle, attr) == 100
        assert getattr(vehicle, "_" + attr + "_unit") == "km"

        # API returns a fresh value but no unit -> unit must stay "km".
        setattr(vehicle, attr, (95, None))
        assert getattr(vehicle, attr) == 95
        assert getattr(vehicle, "_" + attr + "_unit") == "km"

    @pytest.mark.parametrize(
        "attr",
        [
            "total_driving_range",
            "odometer",
            "ev_driving_range",
            "fuel_driving_range",
        ],
    )
    def test_unit_updates_when_api_returns_new_unit(self, vehicle, attr):
        setattr(vehicle, attr, (100, "km"))
        assert getattr(vehicle, "_" + attr + "_unit") == "km"

        setattr(vehicle, attr, (50, "mi"))
        assert getattr(vehicle, attr) == 50
        assert getattr(vehicle, "_" + attr + "_unit") == "mi"

    def test_value_reflects_none_when_api_returns_none_value(self, vehicle):
        # Only the unit is held; a None value is reflected, not preserved.
        vehicle.total_driving_range = (100, "km")
        vehicle.total_driving_range = (None, "km")
        assert vehicle.total_driving_range is None
        assert vehicle.total_driving_range_unit == "km"
