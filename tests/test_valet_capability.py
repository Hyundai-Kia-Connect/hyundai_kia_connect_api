"""Tests for valet mode capability flag and status attribute."""

from hyundai_kia_connect_api.Vehicle import Vehicle


def test_vehicle_has_valet_mode_active_attribute():
    vehicle = Vehicle(id="test-id")
    assert hasattr(vehicle, "valet_mode_active")
    assert vehicle.valet_mode_active is None


def test_vehicle_supports_valet_mode_default_none():
    vehicle = Vehicle(id="test-id")
    assert vehicle.supports.valet_mode is None
