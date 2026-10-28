"""The CCS2 parsers keep the reported climate unit while the setpoint is OFF.

With climate off the car reports Temperature.Value "OFF" next to its unit.
Dropping the unit left consumers guessing (kia_uvo#1809): a Celsius car
could be shown and driven as Fahrenheit.
"""

from hyundai_kia_connect_api.ApiImplType1 import ApiImplType1
from hyundai_kia_connect_api.const import TEMPERATURE_C
from hyundai_kia_connect_api.KiaCciApiEU import KiaCciApiEU
from hyundai_kia_connect_api.Vehicle import Vehicle
from tests.fixture_helpers import load_fixture


def _type1_api() -> ApiImplType1:
    api = ApiImplType1.__new__(ApiImplType1)
    api.data_timezone = None
    api.temperature_range = [x * 0.5 for x in range(28, 60)]
    return api


def test_type1_keeps_unit_when_setpoint_off() -> None:
    state = load_fixture("eu_kia_ev3_2025_ccs2.json")
    assert state["Cabin"]["HVAC"]["Row1"]["Driver"]["Temperature"] == {
        "Value": "OFF",
        "Unit": 0,
    }
    vehicle = Vehicle()
    _type1_api()._update_vehicle_properties_ccs2(vehicle, state)
    assert vehicle._air_temperature_unit == TEMPERATURE_C
    assert vehicle.air_temperature is None


def test_type1_off_keeps_last_setpoint() -> None:
    vehicle = Vehicle()
    vehicle.air_temperature = (22.0, TEMPERATURE_C)
    _type1_api()._update_vehicle_properties_ccs2(
        vehicle, load_fixture("eu_kia_ev3_2025_ccs2.json")
    )
    assert vehicle.air_temperature == 22.0


def test_gspa_keeps_unit_when_setpoint_off() -> None:
    state = load_fixture("eu_kia_gspa_pv5_2026_stored_status.json")
    assert state["Cabin"]["HVAC"]["Row1"]["Driver"]["Temperature"]["Value"] == "OFF"
    vehicle = Vehicle()
    KiaCciApiEU(9, 2, "en")._update_vehicle_properties_ccs2(vehicle, state)
    assert vehicle._air_temperature_unit == TEMPERATURE_C
    assert vehicle.air_temperature is None
