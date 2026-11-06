"""ConnectorFastening.State is exposed as an enum; State 1 drives the plug sensor.

Both CCS2 parsers (ApiImplType1 legacy path and GspaApiEU CCI path) read
the same wire block. State 1 proves the cable is in; 0 is ambiguous —
unplugged vs plugged while the car only locks the connector during a
session (Hyundai-Kia-Connect/kia_uvo#2069) — so the plug sensor reads
True only for state 1 and stays unknown otherwise. Unknown or unexpected
raw values must not raise.
"""

import pytest

from hyundai_kia_connect_api.ApiImplType1 import ApiImplType1
from hyundai_kia_connect_api.const import CONNECTOR_FASTENING_STATE
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


def test_reported_state_populates_enum_plug_on_state_1(parser_api, state, vehicle):
    # EV9 fixture: fastening 0 (ambiguous -> unknown); then 1 (cable in,
    # also true when the car locks only during a session).
    parser_api._update_vehicle_properties_ccs2(vehicle, state)
    assert (
        vehicle.ev_connector_fastening_state is CONNECTOR_FASTENING_STATE.DISCONNECTED
    )
    assert vehicle.ev_battery_is_plugged_in is None

    state["Green"]["ChargingInformation"].update({"ConnectorFastening": {"State": 1}})
    parser_api._update_vehicle_properties_ccs2(vehicle, state)
    assert vehicle.ev_connector_fastening_state is CONNECTOR_FASTENING_STATE.CONNECTED
    assert vehicle.ev_battery_is_plugged_in is True


def test_unknown_value_does_not_crash(parser_api, state, vehicle):
    # Unexpected value -> None (no truthy junk), parser keeps going.
    state["Green"]["ChargingInformation"].update({"ConnectorFastening": {"State": 5}})
    parser_api._update_vehicle_properties_ccs2(vehicle, state)
    assert vehicle.ev_connector_fastening_state is None
    assert vehicle.ev_battery_is_plugged_in is None


def test_unreported_block_leaves_unknown(parser_api, state, vehicle):
    state["Green"]["ChargingInformation"].pop("ConnectorFastening")
    parser_api._update_vehicle_properties_ccs2(vehicle, state)
    assert vehicle.ev_connector_fastening_state is None
    assert vehicle.ev_battery_is_plugged_in is None
