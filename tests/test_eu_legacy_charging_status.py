"""Real EU legacy forced-status captures (flag-0 Kia), charging and idle.

The cached-state snapshot harness always reads CCS2 ``ccs2/carstatus/latest``
for EU. The camelCase ``vehicleStatus`` body only arrives when a non-CCS2 car
is force refreshed (``GET status`` → ``resMsg``), so no ``updates/`` scenario
can drive this parser; the captures are pinned here.
"""

from hyundai_kia_connect_api.const import LENGTH_KILOMETERS
from hyundai_kia_connect_api.KiaUvoApiEU import KiaUvoApiEU
from hyundai_kia_connect_api.Vehicle import Vehicle
from tests.fixture_helpers import load_fixture


def _eu_api() -> KiaUvoApiEU:
    api = KiaUvoApiEU.__new__(KiaUvoApiEU)
    api.data_timezone = KiaUvoApiEU.data_timezone
    api.temperature_range = KiaUvoApiEU.temperature_range
    return api


def test_charging_capture_pins_charging_tree():
    vehicle = Vehicle(id="test-id")
    _eu_api()._update_vehicle_properties(
        vehicle,
        load_fixture(
            "updates/responses/eu_kia_niro_ev_2020_legacy_forced_charging.json"
        ),
    )

    assert vehicle.ev_battery_is_charging is True
    assert vehicle.ev_battery_is_plugged_in == 2
    assert vehicle.ev_battery_percentage == 39
    assert vehicle.ev_charge_limits_ac == 50
    assert vehicle.ev_charge_limits_dc == 60
    assert vehicle.ev_estimated_current_charge_duration == 140
    assert vehicle.ev_estimated_fast_charge_duration == 38
    assert vehicle.ev_estimated_portable_charge_duration == 230
    assert vehicle.ev_estimated_station_charge_duration == 60
    # The capture carries no phase, voltage or charging-current fields
    # (see _fixture_meta.description); charging_power must stay unknown.
    assert vehicle.ev_charging_power is None
    assert vehicle.ev_target_range_charge_AC == 168
    assert vehicle.ev_target_range_charge_AC_unit == LENGTH_KILOMETERS
    assert vehicle.ev_target_range_charge_DC == 168
    assert vehicle.air_temperature == 15.0


def test_idle_capture_pins_idle_counterparts():
    vehicle = Vehicle(id="test-id")
    _eu_api()._update_vehicle_properties(
        vehicle,
        load_fixture("updates/responses/eu_kia_niro_ev_2020_legacy_forced_idle.json"),
    )

    assert vehicle.ev_battery_is_charging is False
    assert vehicle.ev_battery_is_plugged_in == 2
    assert vehicle.ev_estimated_current_charge_duration == 60
    assert vehicle.ev_estimated_fast_charge_duration == 31
    assert vehicle.ev_estimated_portable_charge_duration == 230
    assert vehicle.ev_estimated_station_charge_duration == 60
