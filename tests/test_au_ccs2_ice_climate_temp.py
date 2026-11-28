"""AU CCS2 ICE remote start sends the cabin setpoint like the Kia Connect app."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from hyundai_kia_connect_api.ApiImpl import ClimateRequestOptions
from hyundai_kia_connect_api.const import ENGINE_TYPES
from hyundai_kia_connect_api.KiaUvoApiAU import KiaUvoApiAU
from hyundai_kia_connect_api.Vehicle import Vehicle


@pytest.fixture
def au_api() -> KiaUvoApiAU:
    api = KiaUvoApiAU.__new__(KiaUvoApiAU)
    api._post_engine_command = MagicMock(return_value="MSG-1")
    return api


@pytest.fixture
def ice_ccs2_vehicle() -> Vehicle:
    v = Vehicle(id="vid-123")
    v.engine_type = ENGINE_TYPES.ICE
    v.ccu_ccs2_protocol_support = 1
    return v


def _payload(api: KiaUvoApiAU) -> dict:
    return api._post_engine_command.call_args.args[2]


def test_setpoint_sent_as_string_with_hvac_on(au_api, ice_ccs2_vehicle):
    au_api.start_climate(
        SimpleNamespace(),
        ice_ccs2_vehicle,
        ClimateRequestOptions(set_temp=20.5, duration=10, climate=True),
    )
    payload = _payload(au_api)
    assert payload["hvacTemp"] == "20.5"
    assert payload["hvacTempType"] == 1
    assert payload["tempUnit"] == "C"
    assert payload["hvacCtrl"] == 1


def test_no_setpoint_when_hvac_off(au_api, ice_ccs2_vehicle):
    au_api.start_climate(
        SimpleNamespace(),
        ice_ccs2_vehicle,
        ClimateRequestOptions(set_temp=20.5, climate=False),
    )
    payload = _payload(au_api)
    assert payload["hvacCtrl"] == 0
    assert "hvacTemp" not in payload


def test_no_setpoint_when_not_given(au_api, ice_ccs2_vehicle):
    au_api.start_climate(
        SimpleNamespace(), ice_ccs2_vehicle, ClimateRequestOptions(climate=True)
    )
    assert "hvacTemp" not in _payload(au_api)
