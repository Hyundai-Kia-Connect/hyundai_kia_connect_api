"""Charging.State 0 or a disconnected connector block the RemainTime heuristic.

#2094: cached CCS2 payloads carry Charging.RemainTime > 0 while the car is
unplugged, and both parsers (ApiImplType1 legacy CCS2 path and GspaApiEU CCI
path) derived ev_battery_is_charging from that duration alone. Two captures
pin the blocking values: the unplugged Niro (Charging.State 0, Fastening 0,
RemainTime 70) and the 100%-charged plugged Niro of #1546 (Charging.State 0,
Fastening 1). The official app resolves the charge state from the sequence
fields and falls back to the connector alone: a Fastening.State of 0 is
Disconnected and never charging. The Charging.State == 0 gate rests on the
captures (the app does not read that field). RemainTime > 0 only keeps
signalling charging when neither negative is present (the EV3 charging
fixture carries fastening 1 with a non-zero RemainTime and stays True).
"""

import pytest

from hyundai_kia_connect_api.ApiImplType1 import ApiImplType1
from hyundai_kia_connect_api.KiaCciApiEU import KiaCciApiEU
from hyundai_kia_connect_api.Vehicle import Vehicle
from tests.fixture_helpers import load_fixture

STATE_FIXTURE = "eu_kia_ev9_2024_ccs2.json"


def _type1_api() -> ApiImplType1:
    api = ApiImplType1.__new__(ApiImplType1)
    api.data_timezone = None
    api.temperature_range = [x * 0.5 for x in range(28, 60)]
    return api


def _cci_api() -> KiaCciApiEU:
    return KiaCciApiEU(9, 1, "en")


@pytest.fixture(params=[_type1_api, _cci_api], ids=["type1", "cci"])
def parser_api(request):
    return request.param()


@pytest.fixture
def state():
    return load_fixture(STATE_FIXTURE)


@pytest.fixture
def vehicle() -> Vehicle:
    return Vehicle(id="test-id")


def test_charging_state_zero_blocks_remaintime(parser_api, state, vehicle):
    state["Green"]["ChargingInformation"]["Charging"]["RemainTime"] = 70
    state["Green"]["ChargingInformation"]["Charging"]["State"] = 0
    state["Green"]["ChargingInformation"]["ConnectorFastening"]["State"] = 1
    parser_api._update_vehicle_properties_ccs2(vehicle, state)
    assert vehicle.ev_battery_is_charging is False


def test_fastening_zero_blocks_remaintime(parser_api, state, vehicle):
    # Niro #2094 dump shape: no Charging.State, cable out, stale RemainTime.
    state["Green"]["ChargingInformation"]["Charging"]["RemainTime"] = 70
    state["Green"]["ChargingInformation"]["ConnectorFastening"]["State"] = 0
    parser_api._update_vehicle_properties_ccs2(vehicle, state)
    assert vehicle.ev_battery_is_charging is False


def test_remaintime_charging_preserved_when_connector_in(parser_api, state, vehicle):
    state["Green"]["ChargingInformation"]["Charging"]["RemainTime"] = 70
    state["Green"]["ChargingInformation"]["ConnectorFastening"]["State"] = 1
    parser_api._update_vehicle_properties_ccs2(vehicle, state)
    assert vehicle.ev_battery_is_charging is True


def test_charging_state_present_nonzero_keeps_remaintime(parser_api, state, vehicle):
    state["Green"]["ChargingInformation"]["Charging"]["RemainTime"] = 70
    state["Green"]["ChargingInformation"]["Charging"]["State"] = 1
    state["Green"]["ChargingInformation"]["ConnectorFastening"]["State"] = 1
    parser_api._update_vehicle_properties_ccs2(vehicle, state)
    assert vehicle.ev_battery_is_charging is True
