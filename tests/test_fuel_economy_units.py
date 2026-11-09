"""AverageFuelEconomy.Unit label resolution (Hyundai-Kia-Connect/kia_uvo#2091)."""

from hyundai_kia_connect_api import Vehicle


def test_fuel_economy_unit_labels() -> None:
    vehicle = Vehicle(id="v1", name="test", model="test")
    vehicle.average_fuel_economy_unit = 2
    assert vehicle.average_fuel_economy_accumulated_unit == "MPG"
    assert vehicle.average_fuel_economy_drive_unit == "MPG"
    assert vehicle.average_fuel_economy_after_refuel_unit == "MPG"


def test_fuel_economy_unit_label_none_when_absent_or_unknown() -> None:
    vehicle = Vehicle(id="v1", name="test", model="test")
    assert vehicle.average_fuel_economy_accumulated_unit is None
    vehicle.average_fuel_economy_unit = 11
    assert vehicle.average_fuel_economy_drive_unit is None
