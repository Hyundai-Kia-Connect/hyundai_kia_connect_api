"""ChargingConnector.LockState is exposed as an enum on both CCS2 paths.

PV5 GSPA live semantics (kia_uvo#2069): 0 idle/no cable, 1 unlocked
(cable state unknown), 2 locked, 3/4 transitional, 5 released. Cars
without the block (EV6/EV9 CCS2 stored status) keep None.
"""

import pytest

from hyundai_kia_connect_api.ApiImplType1 import ApiImplType1
from hyundai_kia_connect_api.const import CHARGING_CONNECTOR_LOCK_STATE
from hyundai_kia_connect_api.KiaCciApiEU import KiaCciApiEU
from hyundai_kia_connect_api.Vehicle import Vehicle
from tests.fixture_helpers import load_fixture

STATE_FIXTURE = "eu_kia_gspa_pv5_2026_stored_status.json"


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


def test_lock_state_populates_enum(parser_api, state, vehicle):
    assert vehicle.ev_connector_lock_state is None
    parser_api._update_vehicle_properties_ccs2(vehicle, state)
    assert vehicle.ev_connector_lock_state is CHARGING_CONNECTOR_LOCK_STATE.IDLE


def test_unknown_value_does_not_crash(parser_api, state, vehicle):
    state["Green"]["ChargingConnector"]["LockState"] = 9
    parser_api._update_vehicle_properties_ccs2(vehicle, state)
    assert vehicle.ev_connector_lock_state is None


def test_zero_is_a_state_not_missing(parser_api, state, vehicle):
    # 0 = idle, no cable (2/2 cable-out cases): must not read as absent.
    state["Green"]["ChargingConnector"]["LockState"] = 0
    parser_api._update_vehicle_properties_ccs2(vehicle, state)
    assert vehicle.ev_connector_lock_state is CHARGING_CONNECTOR_LOCK_STATE.IDLE


def test_missing_block_leaves_none(parser_api, state, vehicle):
    del state["Green"]["ChargingConnector"]
    parser_api._update_vehicle_properties_ccs2(vehicle, state)
    assert vehicle.ev_connector_lock_state is None
