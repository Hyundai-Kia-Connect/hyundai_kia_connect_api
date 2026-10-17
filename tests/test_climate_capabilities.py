"""Climate request capabilities must not depend on a temperature reading."""

from copy import deepcopy
from inspect import Parameter, signature
from unittest.mock import MagicMock

import pytest

from hyundai_kia_connect_api.ApiImpl import ApiImpl
from hyundai_kia_connect_api.HyundaiBlueLinkApiUSA import HyundaiBlueLinkApiUSA
from hyundai_kia_connect_api.KiaUvoApiEU import KiaUvoApiEU
from hyundai_kia_connect_api.KiaUvoApiUSA import KiaUvoApiUSA
from hyundai_kia_connect_api.Vehicle import Vehicle
from hyundai_kia_connect_api.VehicleManager import VehicleManager
from tests.fixture_helpers import discover_fixtures, load_fixture


def test_new_capabilities_do_not_shift_positional_vehicle_arguments():
    parameters = signature(Vehicle).parameters
    assert parameters["climate_temperature_unit"].kind == Parameter.KEYWORD_ONLY
    assert parameters["climate_temperature_step"].kind == Parameter.KEYWORD_ONLY
    assert parameters["air_control_is_on"].kind == Parameter.POSITIONAL_OR_KEYWORD


def _discover_vehicle(monkeypatch, api_class):
    """Use real discovery mapping with an entirely offline API stub."""
    api = object.__new__(api_class)
    api.temperature_range = range(62, 82)
    vehicle = Vehicle(id="test", name="test", model="test")
    api.get_vehicles = MagicMock(return_value=[vehicle])
    monkeypatch.setattr(
        VehicleManager, "get_implementation_by_region_brand", lambda *_args: api
    )
    manager = VehicleManager(
        region=3, brand=2, username="test", password="test", pin=""
    )
    manager.initialize_vehicles()
    return api, manager.get_vehicle("test")


@pytest.mark.parametrize(
    ("api_class", "expected_unit", "expected_step"),
    [
        (HyundaiBlueLinkApiUSA, "°F", 1.0),
        (ApiImpl, None, None),
        (KiaUvoApiUSA, None, None),
        (KiaUvoApiEU, None, None),
    ],
)
def test_discovery_supplies_capabilities_without_temperature(
    monkeypatch, api_class, expected_unit, expected_step
):
    _api, vehicle = _discover_vehicle(monkeypatch, api_class)

    assert vehicle.climate_temperature_unit == expected_unit
    assert vehicle.climate_temperature_step == expected_step
    assert vehicle.air_temperature is None
    assert vehicle._air_temperature_unit is None
    assert vehicle.air_control_is_on is None


@pytest.mark.parametrize("fixture_file", discover_fixtures("us_hyundai_"))
@pytest.mark.parametrize("first_status", ["OFF", None, "missing"])
def test_hyundai_capabilities_survive_missing_first_status_and_dropout(
    monkeypatch, fixture_file, first_status
):
    api, vehicle = _discover_vehicle(monkeypatch, HyundaiBlueLinkApiUSA)
    data = deepcopy(load_fixture(fixture_file))
    if first_status == "missing":
        data["vehicleStatus"].pop("airTemp", None)
    else:
        data["vehicleStatus"]["airTemp"] = {"value": first_status}
    api._update_vehicle_properties(vehicle, data)

    assert vehicle.air_temperature is None
    assert vehicle._air_temperature_unit is None
    assert vehicle.climate_temperature_unit == "°F"
    assert vehicle.climate_temperature_step == 1.0

    data["vehicleStatus"]["airTemp"] = {"value": "70", "unit": 1}
    api._update_vehicle_properties(vehicle, data)
    data["vehicleStatus"]["airTemp"] = {"value": "OFF"}
    api._update_vehicle_properties(vehicle, data)

    # Keep the existing telemetry semantics and the learned-unit fix.
    assert vehicle.air_temperature == 70
    assert vehicle._air_temperature_unit == "°F"
    assert vehicle.climate_temperature_unit == "°F"
    assert vehicle.climate_temperature_step == 1.0
