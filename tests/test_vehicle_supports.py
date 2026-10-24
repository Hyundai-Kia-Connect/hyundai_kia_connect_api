"""Tests for the vehicle.supports.<item> capability model."""

import dataclasses
from unittest.mock import patch

import pytest

from hyundai_kia_connect_api import VehicleSupports
from hyundai_kia_connect_api.ApiImpl import ApiImpl
from hyundai_kia_connect_api.ApiImplType1 import ApiImplType1
from hyundai_kia_connect_api.HyundaiBlueLinkApiBR import HyundaiBlueLinkApiBR
from hyundai_kia_connect_api.HyundaiCciApiEU import HyundaiCciApiEU
from hyundai_kia_connect_api.KiaCciApiEU import KiaCciApiEU
from hyundai_kia_connect_api.Token import Token
from hyundai_kia_connect_api.Vehicle import Vehicle
from hyundai_kia_connect_api.VehicleManager import VehicleManager


def _resolve(api_cls) -> VehicleSupports:
    return api_cls.get_vehicle_supports(api_cls.__new__(api_cls), Vehicle())


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
    ("api_cls", "expected"),
    [
        (
            ApiImpl,
            VehicleSupports(
                window_control=False, valet_mode=False, surround_view=False
            ),
        ),
        (
            ApiImplType1,
            VehicleSupports(window_control=True, valet_mode=True, surround_view=False),
        ),
        (
            HyundaiBlueLinkApiBR,
            VehicleSupports(window_control=True, valet_mode=False, surround_view=False),
        ),
        (
            HyundaiCciApiEU,
            VehicleSupports(window_control=True, valet_mode=True, surround_view=True),
        ),
        (
            KiaCciApiEU,
            VehicleSupports(window_control=True, valet_mode=True, surround_view=False),
        ),
    ],
)
def test_get_vehicle_supports_per_region(api_cls, expected):
    assert _resolve(api_cls) == expected


def test_api_holds_no_flat_supports_attributes():
    """Support is resolved by get_vehicle_supports, not stored on the API."""
    for api_cls in (
        ApiImpl,
        ApiImplType1,
        HyundaiBlueLinkApiBR,
        HyundaiCciApiEU,
        KiaCciApiEU,
    ):
        flat = [name for name in dir(api_cls) if name.startswith("supports_")]
        assert flat == [], api_cls.__name__


def test_items_without_region_attribute_stay_undetermined():
    supports = _resolve(ApiImpl)
    assert supports.sunroof is None
    assert supports.hazard_lights_and_horn is None


def test_vehicle_manager_uses_per_vehicle_override():
    class PerVehicleApi(ApiImpl):
        def get_vehicle_supports(self, vehicle):
            supports = super().get_vehicle_supports(vehicle)
            supports.surround_view = vehicle.id == "with-svm"
            return supports

    with_svm, without_svm = Vehicle(id="with-svm"), Vehicle(id="without-svm")
    api = PerVehicleApi()
    manager = VehicleManager.__new__(VehicleManager)
    manager.api = api
    manager.token = Token(access_token="x", refresh_token="y", device_id="z")
    manager.vehicles = {}

    with patch.object(api, "get_vehicles", return_value=[with_svm, without_svm]):
        manager.initialize_vehicles()

    assert manager.vehicles["with-svm"].supports.surround_view is True
    assert manager.vehicles["without-svm"].supports.surround_view is False


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
    with pytest.deprecated_call():
        setattr(vehicle, alias, True)
    assert getattr(vehicle.supports, item) is True
    with pytest.deprecated_call():
        assert getattr(vehicle, alias) is True
