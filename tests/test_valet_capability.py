"""Tests for valet mode capability flag and status attribute."""

from unittest.mock import MagicMock

from hyundai_kia_connect_api.ApiImpl import ApiImpl
from hyundai_kia_connect_api.ApiImplType1 import ApiImplType1
from hyundai_kia_connect_api.Token import Token
from hyundai_kia_connect_api.Vehicle import Vehicle
from hyundai_kia_connect_api.VehicleManager import VehicleManager


def test_vehicle_has_valet_mode_active_attribute():
    vehicle = Vehicle()
    assert hasattr(vehicle, "valet_mode_active")
    assert vehicle.valet_mode_active is None


def test_vehicle_has_supports_valet_mode_attribute():
    vehicle = Vehicle()
    assert hasattr(vehicle, "supports_valet_mode")
    assert vehicle.supports_valet_mode is None


def test_base_api_impl_does_not_support_valet_mode():
    assert ApiImpl.supports_valet_mode is False


def test_type1_api_impl_supports_valet_mode():
    assert ApiImplType1.supports_valet_mode is True


def test_vehicle_manager_copies_supports_valet_mode():
    vehicle = Vehicle()
    vehicle.id = "test-vehicle"

    fake_api = MagicMock()
    fake_api.supports_valet_mode = True
    fake_api.get_vehicles.return_value = [vehicle]

    manager = VehicleManager.__new__(VehicleManager)
    manager.api = fake_api
    manager.token = Token(access_token="x", refresh_token="y", device_id="z")
    manager.vehicles = {}

    manager.initialize_vehicles()

    assert manager.vehicles["test-vehicle"].supports_valet_mode is True
