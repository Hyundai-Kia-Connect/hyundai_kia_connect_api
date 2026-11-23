"""Unmapped distance-unit codes must not crash the EU legacy status parser.

The CCS2 parsers report an unmapped DTE unit (e.g. 70) as an unknown range.
The legacy parser, used by the force refresh of non-CCS2 EU cars, indexed
DISTANCE_UNITS directly and raised KeyError instead.
"""

from hyundai_kia_connect_api.const import LENGTH_KILOMETERS
from hyundai_kia_connect_api.KiaUvoApiEU import KiaUvoApiEU
from hyundai_kia_connect_api.Vehicle import Vehicle
from tests.fixture_helpers import load_fixture

FIXTURE_FILE = "updates/responses/eu_kia_ev6_2023_with_soc.json"


def _eu_api() -> KiaUvoApiEU:
    api = KiaUvoApiEU.__new__(KiaUvoApiEU)
    api.data_timezone = KiaUvoApiEU.data_timezone
    api.temperature_range = KiaUvoApiEU.temperature_range
    return api


def _set_units(data: dict, code: int) -> None:
    status = data["vehicleStatus"]
    data["odometer"]["unit"] = code
    status["dte"]["unit"] = code
    for distance in status["evStatus"]["drvDistance"]:
        for value in distance["rangeByFuel"].values():
            value["unit"] = code
    for target in status["evStatus"]["reservChargeInfos"]["targetSOClist"]:
        target["dte"]["rangeByFuel"]["totalAvailableRange"]["unit"] = code


def test_known_unit_keeps_distances():
    vehicle = Vehicle(id="test-id")
    _eu_api()._update_vehicle_properties(vehicle, load_fixture(FIXTURE_FILE))

    assert vehicle.odometer == 15234
    assert vehicle.odometer_unit == LENGTH_KILOMETERS
    assert vehicle.total_driving_range is not None
    assert vehicle.total_driving_range_unit == LENGTH_KILOMETERS
    assert vehicle.ev_target_range_charge_AC is not None


def test_unknown_unit_leaves_distances_unknown(caplog):
    data = load_fixture(FIXTURE_FILE)
    _set_units(data, 70)

    vehicle = Vehicle(id="test-id")
    _eu_api()._update_vehicle_properties(vehicle, data)

    assert vehicle.odometer is None
    assert vehicle.total_driving_range is None
    assert vehicle.ev_driving_range is None
    assert vehicle.ev_target_range_charge_AC is None
    assert vehicle.ev_target_range_charge_DC is None
    assert vehicle.ev_battery_percentage is not None
    assert "Unknown distance unit 70" in caplog.text


def test_unknown_dte_unit_leaves_fuel_range_unknown():
    data = load_fixture(FIXTURE_FILE)
    data["vehicleStatus"]["dte"] = {"value": 300, "unit": 70}

    vehicle = Vehicle(id="test-id")
    _eu_api()._update_vehicle_properties(vehicle, data)

    assert vehicle.fuel_driving_range is None
