"""Tests for CCS2 ev_battery_precondition_enabled read mapping.

EV (IONIQ 5 / IONIQ 6) reports Green.BatteryManagement.BatteryPreCondition.Status
— a configuration setting (stable across ignition/climate state), not a runtime
flag (BatteryConditioning is the runtime flag). HEV (Santa Fe) reports only
WinterModeOperation; "winter mode" is itself the battery preconditioning /
winter-heating toggle there, so it is used as a fallback. This preserves
existing HEV behaviour (no regression) while making the precondition sensor
appear on EVs that report BatteryPreCondition.

Status enum — mapping matches the official Hyundai app
(BatteryConditioningModel, option==2 vehicles):
  Status = 0 / 2 / 6 -> preconditioning OFF
  Status = 3 / 4     -> preconditioning ON
This supersedes the earlier reading of the kia_uvo #1652 IONIQ 5 dumps
(2026-07-12) that Status=2 meant "enabled" — that was an inference from two
dumps without UI confirmation. kia_uvo #1823 (Ioniq 6 2026, ccNC) captured
Status=2 with preconditioning OFF in the vehicle settings; the "toggling
while driving" reported there was Status moving between 2/3/4.

kia_uvo #1823 also established that WinterModeOperation is NOT a user-facing
"Winter Mode" toggle on EVs (the app never exposes it as one), so when
BatteryPreCondition.Status is present, ev_battery_winter_mode must stay None
instead of bool(WinterModeOperation).

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


def _ev_state(status: int) -> dict:
    """EV state with BatteryPreCondition.Status set (no WinterModeOperation)."""
    return _state(
        {
            "SoH": {"Ratio": 100},
            "BatteryRemain": {"Value": 109843.2, "Ratio": 42, "Unit": "kJ"},
            "BatteryConditioning": 0,
            "BatteryPreCondition": {"Status": status, "TemperatureLevel": 2},
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

# HEV (Santa Fe): WinterModeOperation = 1, no BatteryPreCondition — the other
# side of the no-regression guarantee (fallback maps 1 -> True/True).
_HEV_WINTER_MODE_ON = _state(
    {
        "SoH": {"Ratio": 100},
        "WinterModeOperation": 1,
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

    @pytest.mark.parametrize(
        ("status", "expected_enabled"),
        [
            (0, False),
            (2, False),  # kia_uvo #1823 dump: precondition OFF in settings
            (3, True),
            (4, True),
            (6, False),
        ],
    )
    def test_ioniq5_precondition_status_enum(self, ccs2_api, status, expected_enabled):
        """EV: BatteryPreCondition.Status maps 0/2/6 -> off, 3/4 -> on."""
        vehicle = Vehicle()
        ccs2_api._update_vehicle_properties_ccs2(vehicle, _ev_state(status))
        assert vehicle.ev_battery_precondition_enabled is expected_enabled
        # Status present -> WinterModeOperation is not mapped to a boolean.
        assert vehicle.ev_battery_winter_mode is None

    def test_ioniq6_ccnc_1823_dump_case(self, ccs2_api):
        """kia_uvo #1823 exact dump (Ioniq 6 2026, ccNC).

        WinterModeOperation=1 alongside BatteryPreCondition.Status=2, with
        Winter Mode NOT armed and preconditioning OFF in the vehicle settings.
        Precondition must read OFF (app mapping: 2 -> off) and winter_mode
        must not become a boolean from WinterModeOperation on an EV.
        """
        vehicle = Vehicle()
        state = _state(
            {
                "SoH": {"Ratio": 100},
                "WinterModeOperation": 1,
                "BatteryConditioning": 0,
                "HeatingState": 0,
                "BatteryPreCondition": {"Status": 2, "TemperatureLevel": 1},
                "BatteryRemain": {"Value": 109843.2, "Ratio": 42, "Unit": "kJ"},
            }
        )
        ccs2_api._update_vehicle_properties_ccs2(vehicle, state)
        assert vehicle.ev_battery_precondition_enabled is False
        assert vehicle.ev_battery_winter_mode is None

    def test_ioniq5_precondition_disabled_from_battery_precondition_status(
        self, ccs2_api
    ):
        """EV (IONIQ 5): BatteryPreCondition.Status=0 -> precondition disabled.

        Confirmed via reporter dump (kia_uvo #1652, 2026-07-12): with
        preconditioning turned OFF in the myHyundai app, Status reads 0.
        """
        vehicle = Vehicle()
        ccs2_api._update_vehicle_properties_ccs2(vehicle, _ev_state(0))
        assert vehicle.ev_battery_precondition_enabled is False
        assert vehicle.ev_battery_winter_mode is None

    def test_hev_precondition_falls_back_to_winter_mode_off(self, ccs2_api):
        """HEV: no BatteryPreCondition -> fallback to WinterModeOperation=0 ->
        False. No-regression guarantee: existing HEV behaviour is unchanged."""
        vehicle = Vehicle()
        ccs2_api._update_vehicle_properties_ccs2(vehicle, _HEV_WINTER_MODE_OFF)
        assert vehicle.ev_battery_precondition_enabled is False
        assert vehicle.ev_battery_winter_mode is False

    def test_hev_precondition_falls_back_to_winter_mode_on(self, ccs2_api):
        """HEV: no BatteryPreCondition -> fallback to WinterModeOperation=1 ->
        True for both fields. No-regression guarantee for the armed case."""
        vehicle = Vehicle()
        ccs2_api._update_vehicle_properties_ccs2(vehicle, _HEV_WINTER_MODE_ON)
        assert vehicle.ev_battery_precondition_enabled is True
        assert vehicle.ev_battery_winter_mode is True

    def test_precondition_field_exists_in_vehicle(self):
        v = Vehicle()
        assert hasattr(v, "ev_battery_precondition_enabled")
        assert v.ev_battery_precondition_enabled is None

    def test_winter_mode_field_exists_in_vehicle(self):
        v = Vehicle()
        assert hasattr(v, "ev_battery_winter_mode")
        assert v.ev_battery_winter_mode is None
