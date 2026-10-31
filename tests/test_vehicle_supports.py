"""Tests for the vehicle.supports.<item> capability model."""

import dataclasses
from typing import Any

import pytest

from hyundai_kia_connect_api import VehicleSupports
from hyundai_kia_connect_api.ApiImpl import ApiImpl
from hyundai_kia_connect_api.ApiImplType1 import ApiImplType1
from hyundai_kia_connect_api.GenesisCciApiEU import GenesisCciApiEU
from hyundai_kia_connect_api.HyundaiBlueLinkApiBR import HyundaiBlueLinkApiBR
from hyundai_kia_connect_api.HyundaiCciApiEU import HyundaiCciApiEU
from hyundai_kia_connect_api.KiaCciApiEU import KiaCciApiEU
from hyundai_kia_connect_api.Vehicle import Vehicle

_SPA_ENTRY = {
    "vehicleId": "vehicle-id",
    "nickname": "nickname",
    "vehicleName": "model",
    "regDate": "2024-01-01",
    "vin": "VIN",
    "type": "EV",
}
_CCI_ENTRY = {"ccspCarId": "vehicle-id", "vin": "VIN", "isEv": True}


def _spa_vehicle(api_cls: Any) -> Vehicle:
    vehicle: Vehicle = api_cls.__new__(api_cls)._vehicle_from_spa_entry(_SPA_ENTRY)
    return vehicle


def _cci_vehicle(api_cls: Any) -> Vehicle:
    vehicles: list[Vehicle] = api_cls.__new__(api_cls)._parse_vehicles_from_cci(
        [_CCI_ENTRY]
    )
    return vehicles[0]


def test_vehicle_supports_defaults_to_undetermined():
    vehicle = Vehicle()
    assert vehicle.supports == VehicleSupports()
    assert all(
        getattr(vehicle.supports, f.name) is None
        for f in dataclasses.fields(VehicleSupports)
    )


def test_vehicle_supports_not_shared_between_vehicles():
    first, second = Vehicle(), Vehicle()
    first.supports.surround_view = True
    assert second.supports.surround_view is None


@pytest.mark.parametrize(
    ("build", "expected"),
    [
        (
            lambda: _spa_vehicle(ApiImplType1),
            VehicleSupports(window_control=True, valet_mode=True, surround_view=False),
        ),
        (
            lambda: _spa_vehicle(HyundaiBlueLinkApiBR),
            VehicleSupports(window_control=True, valet_mode=False, surround_view=False),
        ),
        (
            lambda: _cci_vehicle(HyundaiCciApiEU),
            VehicleSupports(window_control=True, valet_mode=True, surround_view=True),
        ),
        (
            lambda: _cci_vehicle(KiaCciApiEU),
            VehicleSupports(window_control=True, valet_mode=True, surround_view=False),
        ),
        (
            lambda: _cci_vehicle(GenesisCciApiEU),
            VehicleSupports(window_control=False, valet_mode=True, surround_view=False),
        ),
    ],
    ids=["type1", "br", "eu_cci_hyundai", "eu_cci_kia", "eu_cci_genesis"],
)
def test_region_sets_supports_on_parsed_vehicle(build, expected):
    assert build().supports == expected


def test_api_holds_no_supports_attributes():
    """Support lives on vehicle.supports, not on the API."""
    for api_cls in (
        ApiImpl,
        ApiImplType1,
        HyundaiBlueLinkApiBR,
        HyundaiCciApiEU,
        KiaCciApiEU,
        GenesisCciApiEU,
    ):
        flat = [name for name in dir(api_cls) if "supports" in name]
        assert flat == [], api_cls.__name__


@pytest.mark.parametrize(
    ("alias", "item"),
    [
        ("supports_window_control", "window_control"),
        ("supports_valet_mode", "valet_mode"),
        ("supports_svm", "surround_view"),
    ],
)
def test_deprecated_flat_alias_reads_and_writes_supports(alias, item):
    vehicle = Vehicle()
    supports = vehicle.supports
    with pytest.deprecated_call():
        setattr(vehicle, alias, True)
    assert vehicle.supports is supports
    assert getattr(vehicle.supports, item) is True
    with pytest.deprecated_call():
        assert getattr(vehicle, alias) is True
