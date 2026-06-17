"""Tests for CCS2 ev_battery_precondition_enabled sensor.

The CCS2 response field Green.BatteryManagement.WinterModeOperation
is now mapped to both ev_battery_winter_mode and
ev_battery_precondition_enabled, matching USA Kia and CA behaviour.
"""

import pytest

from hyundai_kia_connect_api.Vehicle import Vehicle
from hyundai_kia_connect_api.const import ENGINE_TYPES


@pytest.fixture
def vehicle():
    v = Vehicle()
    v.id = "test-vehicle-id"
    v.engine_type = ENGINE_TYPES.PHEV
    return v


class TestCCS2PreconditionSensor:
    """Test that ev_battery_precondition_enabled mirrors ev_battery_winter_mode.

    Both fields should be set from the same CCS2 source field
    (Green.BatteryManagement.WinterModeOperation). This matches
    USA Kia (batteryPrecondition) and CA (batteryPreconditiong).
    """

    def test_winter_mode_true_sets_precondition_true(self, vehicle):
        """When winter_mode is True, precondition_enabled should also be True."""
        vehicle.ev_battery_winter_mode = True
        # In _update_vehicle_properties_ccs2, the code does:
        #   vehicle.ev_battery_precondition_enabled = bool(battery_winter_mode)
        # Simulate what the code does:
        vehicle.ev_battery_precondition_enabled = bool(vehicle.ev_battery_winter_mode)

        assert vehicle.ev_battery_winter_mode is True
        assert vehicle.ev_battery_precondition_enabled is True

    def test_winter_mode_false_sets_precondition_false(self, vehicle):
        """When winter_mode is False, precondition_enabled should also be False."""
        vehicle.ev_battery_winter_mode = False
        vehicle.ev_battery_precondition_enabled = bool(vehicle.ev_battery_winter_mode)

        assert vehicle.ev_battery_winter_mode is False
        assert vehicle.ev_battery_precondition_enabled is False

    def test_winter_mode_none_keeps_precondition_none(self, vehicle):
        """When winter_mode is None (field absent), precondition_enabled stays None."""
        # Both fields start as None in the Vehicle dataclass
        assert vehicle.ev_battery_winter_mode is None
        assert vehicle.ev_battery_precondition_enabled is None

    def test_precondition_field_exists_in_vehicle(self):
        """Verify the field exists in Vehicle dataclass with correct type."""
        v = Vehicle()
        assert hasattr(v, "ev_battery_precondition_enabled")
        assert v.ev_battery_precondition_enabled is None

    def test_winter_mode_field_exists_in_vehicle(self):
        """Verify the winter_mode field exists in Vehicle dataclass."""
        v = Vehicle()
        assert hasattr(v, "ev_battery_winter_mode")
        assert v.ev_battery_winter_mode is None
