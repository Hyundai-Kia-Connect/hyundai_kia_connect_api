"""Regression test: unmapped seat heater/vent codes must not crash parsing.

Some vehicles (e.g. Genesis G80 EU) report ``seatHeaterVentState`` codes that
are not present in ``const.SEAT_STATUS`` (observed: 15). The seat status lookup
must degrade to ``None`` instead of raising ``KeyError`` and aborting the whole
vehicle update (which leaves every entity unavailable).
"""

from hyundai_kia_connect_api.KiaUvoApiEU import KiaUvoApiEU
from hyundai_kia_connect_api.Vehicle import Vehicle

from tests.fixture_helpers import load_fixture


def _eu_api() -> KiaUvoApiEU:
    api = KiaUvoApiEU.__new__(KiaUvoApiEU)
    api.data_timezone = KiaUvoApiEU.data_timezone
    api.temperature_range = KiaUvoApiEU.temperature_range
    return api


def test_unknown_seat_status_code_does_not_raise():
    data = load_fixture("eu_kia_ev6_2023_with_soc.json")
    # Unmapped code reported by some vehicles (e.g. Genesis G80 EU -> 15)
    data["vehicleStatus"]["seatHeaterVentState"]["rlSeatHeatState"] = 15

    vehicle = Vehicle()
    # Must not raise KeyError on the unmapped code.
    _eu_api()._update_vehicle_properties(vehicle, data)

    # Unknown code degrades to None ...
    assert vehicle.rear_left_seat_status is None
    # ... while known codes still map correctly (fixture has 0 -> "Off").
    assert vehicle.front_left_seat_status == "Off"
