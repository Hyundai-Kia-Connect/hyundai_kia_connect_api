"""Regression tests for HyundaiBlueLinkApiUSA._update_vehicle_properties.

Fixture-driven parsing is covered by tests/test_fixture_parsing.py; this
module holds targeted cases built from minimal inline payloads.
"""

import pytest

from hyundai_kia_connect_api.HyundaiBlueLinkApiUSA import HyundaiBlueLinkApiUSA
from hyundai_kia_connect_api.Vehicle import Vehicle
from tests.fixture_helpers import PARSERS


@pytest.fixture
def bluelink_api() -> HyundaiBlueLinkApiUSA:
    return PARSERS["usa_hyundai"].api()


# ---------------------------------------------------------------------------
# Regression: kia_uvo #1790 — airTemp "OFF" (climate off) must not mask
# ---------------------------------------------------------------------------


@pytest.fixture
def bluelink_status_with_air_temp_off():
    """Minimal Hyundai USA cached-status payload with airTemp.value == OFF."""
    return {
        "vehicleStatus": {
            "airTemp": {"value": "OFF", "unit": 1, "hvacTempType": 1},
        },
    }


def test_bluelink_dc_charging_power_reads_real_time_power(bluelink_api):
    """`ev_charging_power` must come from `realTimePower`, not `batteryStndChrgPower`.

    `batteryStndChrgPower` is *standard* (AC) charge power. During a DC fast
    charge it keeps reporting the AC rate, so a Hyundai USA vehicle could never
    observe a fast charge at all — the field is flat across the one event it is
    being read for. `realTimePower` is instantaneous, and is already the source
    used by KiaUvoApiUSA and ApiImplType1 for this same attribute.

    The two values are deliberately different here: with the old key this
    asserts 10.9 (the vehicle's AC ceiling) instead of 58.7, which is the
    signature of the bug rather than a rounding difference.
    """
    vehicle = Vehicle()
    state = {
        "vehicleStatus": {
            "evStatus": {
                "batteryCharge": True,
                "batteryStndChrgPower": 10.9,
                "realTimePower": 58.7,
            },
        },
    }
    bluelink_api._update_vehicle_properties(vehicle, state)
    assert vehicle.ev_charging_power == 58.7


def test_bluelink_charging_power_none_when_real_time_power_absent(bluelink_api):
    """`realTimePower` is absent while idle, so `ev_charging_power` is None.

    Reading `realTimePower` directly — as KiaUvoApiUSA does — returns None when
    the field is missing, rather than substituting the stale AC rate carried by
    `batteryStndChrgPower`.
    """
    vehicle = Vehicle()
    state = {
        "vehicleStatus": {
            "evStatus": {
                "batteryCharge": False,
                "batteryStndChrgPower": 0.0,
                "realTimePower": None,
            },
        },
    }
    bluelink_api._update_vehicle_properties(vehicle, state)
    assert vehicle.ev_charging_power is None


def test_bluelink_air_temp_off_yields_none(
    bluelink_api, bluelink_status_with_air_temp_off
):
    """Regression for kia_uvo #1790: when climate is off, the USA backend
    returns airTemp.value == "OFF". The setter must NOT be called with a
    non-numeric string; air_temperature and the raw value slot stay None so
    the kia_uvo sensor reports `unknown` (data unknown), not a fake setpoint.

    Note: float_or_none("OFF") already yields None, so asserting only on
    air_temperature would pass before the fix. The behavioral difference
    the conformance fixes is that the setter is not called at all — so the
    raw value slot (_air_temperature_value) stays None instead of holding
    the string "OFF". Assert on that side effect as the real TDD gate."""
    vehicle = Vehicle()
    bluelink_api._update_vehicle_properties(vehicle, bluelink_status_with_air_temp_off)
    assert vehicle.air_temperature is None
    assert vehicle._air_temperature_value is None


@pytest.mark.parametrize(
    ("details_odometer", "status_odometer", "expected"),
    [
        (12000, 12035, 12035.0),
        (14000.4, 9000, 14000.4),
        (12000, None, 12000.0),
        (None, 12035, 12035.0),
        ("unknown", 12035, 12035.0),
        (None, None, None),
    ],
)
def test_bluelink_odometer_uses_highest_valid_value(
    bluelink_api, details_odometer, status_odometer, expected
):
    """Use the freshest valid odometer when Hyundai's USA fields disagree."""
    state = {
        "vehicleDetails": {"odometer": details_odometer},
        "vehicleStatus": {"odometer": status_odometer},
    }

    vehicle = Vehicle()
    bluelink_api._update_vehicle_properties(vehicle, state)

    assert vehicle.odometer == expected
