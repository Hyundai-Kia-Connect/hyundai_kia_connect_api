"""CA seat heater states come from status.seatHeaterVentInfo (driver/assist keys).

Values below are the ones reported in issue #1620 (2026 IONIQ 5, climate
started with flseat=8, frseat=6, no rear heaters), applied to a real CA
lstvhclsts capture.
"""

from hyundai_kia_connect_api.KiaUvoApiCA import KiaUvoApiCA
from hyundai_kia_connect_api.Vehicle import Vehicle
from tests.fixture_helpers import load_fixture


def test_seat_heater_vent_info_maps_driver_to_front_left() -> None:
    state = load_fixture("updates/responses/ca_kia_lstvhclsts_881.json")["result"]
    state["status"]["seatHeaterVentInfo"] = {
        "drvSeatHeatState": 8,
        "astSeatHeatState": 6,
        "rlSeatHeatState": 2,
        "rrSeatHeatState": 2,
    }
    api = KiaUvoApiCA.__new__(KiaUvoApiCA)
    api.data_timezone = KiaUvoApiCA.data_timezone
    vehicle = Vehicle(id="test-id")

    api._update_vehicle_properties_base(vehicle, state)

    assert vehicle.front_left_seat_status == "High Heat"
    assert vehicle.front_right_seat_status == "Low Heat"
    assert vehicle.rear_left_seat_status == "Off"
    assert vehicle.rear_right_seat_status == "Off"
