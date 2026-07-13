"""Tests for CCS2 ev_battery_precondition_enabled read mapping.

EV (IONIQ 5) reports Green.BatteryManagement.BatteryPreCondition.Status —
a configuration setting (stable across ignition/climate state), not a runtime
flag (BatteryConditioning is the runtime flag). HEV (Santa Fe) reports only
WinterModeOperation; "winter mode" is itself the battery preconditioning /
winter-heating toggle there, so it is used as a fallback. This preserves
existing HEV behaviour (no regression) while making the precondition sensor
appear on EVs that report BatteryPreCondition.

Status enum (confirmed via reporter dumps, kia_uvo #1652, 2026-07-12):
  Status = 0 -> preconditioning disabled in vehicle settings
  Status = 2 -> preconditioning enabled in vehicle settings
Both dumps (disabled and enabled) have no WinterModeOperation key -> the
#1187 alias (precondition = bool(WinterModeOperation)) never produced a
sensor on EVs.

The CCS2 states below are inlined (not loaded from tests/fixtures/) because
they are minimal, target-specific, and derived from live reporter dumps that
must not be committed verbatim (PII). Envelope fields (Date/Offset/Drivetrain)
are present only where _update_vehicle_properties_ccs2 reads them
unconditionally (DTE.Total/Unit are coerced with float()/indexed without a
None guard).
"""

import pytest

from hyundai_kia_connect_api.ApiImplType1 import ApiImplType1
from hyundai_kia_connect_api.Vehicle import Vehicle

# Minimal CCS2 state envelope shared by all cases. Drivetrain.FuelSystem.DTE is
# read unconditionally (float() + DISTANCE_UNITS index), so it must be present.
_BASE_ENVELOPE = {
    "Drivetrain": {
        "Odometer": 5352.6,
        "FuelSystem": {"DTE": {"Unit": 1, "Total": 225}},
    },
    "DrivingReady": 1,
    "Date": "20260712120000",
    "Offset": 120,
}


def _state(battery_management: dict) -> dict:
    state = dict(_BASE_ENVELOPE)
    state["Green"] = {
        "DrivingReady": 1,
        "BatteryManagement": battery_management,
    }
    return state


# EV (IONIQ 5): preconditioning ENABLED in vehicle settings (kia_uvo #1652,
# 2026-07-12). BatteryPreCondition.Status = 2, no WinterModeOperation.
_IONIQ5_ENABLED = _state(
    {
        "SoH": {"Ratio": 100},
        "BatteryRemain": {"Value": 109843.2, "Ratio": 42, "Unit": "kJ"},
        "BatteryConditioning": 0,
        "BatteryPreCondition": {"Status": 2, "TemperatureLevel": 2},
        "BatteryCapacity": {"Value": 302400, "Unit": "kJ"},
    }
)

# EV (IONIQ 5): preconditioning DISABLED in vehicle settings (kia_uvo #1652,
# 2026-07-12). BatteryPreCondition.Status = 0, no WinterModeOperation.
_IONIQ5_DISABLED = _state(
    {
        "SoH": {"Ratio": 100},
        "BatteryRemain": {"Value": 125107.2, "Ratio": 47.5, "Unit": "kJ"},
        "BatteryConditioning": 0,
        "BatteryPreCondition": {"Status": 0, "TemperatureLevel": 2},
        "BatteryCapacity": {"Value": 302400, "Unit": "kJ"},
    }
)

# HEV (Santa Fe): WinterModeOperation = 0, no BatteryPreCondition. This is the
# no-regression case — the fallback path must reproduce today's behaviour.
_HEV_WINTER_MODE_OFF = _state(
    {
        "SoH": {"Ratio": 100},
        "WinterModeOperation": 0,
        "BatteryRemain": {"Value": 0, "Ratio": 49.5, "Unit": "kJ"},
    }
)


@pytest.fixture
def ccs2_api() -> ApiImplType1:
    api = ApiImplType1.__new__(ApiImplType1)
    api.data_timezone = None
    api.temperature_range = [x * 0.5 for x in range(28, 60)]
    return api


class TestCCS2PreconditionSensor:
    """Precondition read from BatteryPreCondition.Status (EV) with
    WinterModeOperation fallback (HEV)."""

    def test_ioniq5_precondition_enabled_from_battery_precondition_status(
        self, ccs2_api
    ):
        """EV (IONIQ 5): BatteryPreCondition.Status=2 -> precondition enabled."""
        vehicle = Vehicle()
        ccs2_api._update_vehicle_properties_ccs2(vehicle, _IONIQ5_ENABLED)
        assert vehicle.ev_battery_precondition_enabled is True
        # IONIQ 5 does not report WinterModeOperation -> winter_mode stays None.
        assert vehicle.ev_battery_winter_mode is None

    def test_ioniq5_precondition_disabled_from_battery_precondition_status(
        self, ccs2_api
    ):
        """EV (IONIQ 5): BatteryPreCondition.Status=0 -> precondition disabled.

        Confirmed via reporter dump (kia_uvo #1652, 2026-07-12): with
        preconditioning turned OFF in the myHyundai app, Status reads 0.
        """
        vehicle = Vehicle()
        ccs2_api._update_vehicle_properties_ccs2(vehicle, _IONIQ5_DISABLED)
        assert vehicle.ev_battery_precondition_enabled is False
        assert vehicle.ev_battery_winter_mode is None

    def test_hev_precondition_falls_back_to_winter_mode(self, ccs2_api):
        """HEV: no BatteryPreCondition -> fallback to WinterModeOperation=0 ->
        False. No-regression guarantee: existing HEV behaviour is unchanged."""
        vehicle = Vehicle()
        ccs2_api._update_vehicle_properties_ccs2(vehicle, _HEV_WINTER_MODE_OFF)
        assert vehicle.ev_battery_precondition_enabled is False
        assert vehicle.ev_battery_winter_mode is False

    def test_precondition_field_exists_in_vehicle(self):
        v = Vehicle()
        assert hasattr(v, "ev_battery_precondition_enabled")
        assert v.ev_battery_precondition_enabled is None

    def test_winter_mode_field_exists_in_vehicle(self):
        v = Vehicle()
        assert hasattr(v, "ev_battery_winter_mode")
        assert v.ev_battery_winter_mode is None
