"""Snapshot tests for the Vehicle object across all API implementations.

Every fixture in ``tests/fixtures`` is parsed by the parser its filename
prefix selects, and the full parsed Vehicle is compared to a syrupy snapshot.
A fixture with no parser mapped fails, so new fixtures can't be skipped.

To update snapshots after an intentional change::

    pytest tests/test_vehicle_snapshots.py --snapshot-update
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any

import pytest

from hyundai_kia_connect_api.ApiImplType1 import ApiImplType1
from hyundai_kia_connect_api.HyundaiBlueLinkApiBR import HyundaiBlueLinkApiBR
from hyundai_kia_connect_api.HyundaiBlueLinkApiUSA import HyundaiBlueLinkApiUSA
from hyundai_kia_connect_api.KiaCciApiEU import KiaCciApiEU
from hyundai_kia_connect_api.KiaUvoApiAU import KiaUvoApiAU
from hyundai_kia_connect_api.KiaUvoApiCA import KiaUvoApiCA
from hyundai_kia_connect_api.KiaUvoApiCN import KiaUvoApiCN
from hyundai_kia_connect_api.KiaUvoApiEU import KiaUvoApiEU
from hyundai_kia_connect_api.KiaUvoApiUSA import KiaUvoApiUSA
from hyundai_kia_connect_api.Vehicle import Vehicle
from tests.fixture_helpers import discover_fixtures, load_fixture
from tests.vehicle_snapshot_serializer import vehicle_to_dict

if TYPE_CHECKING:
    from syrupy.assertion import SnapshotAssertion

_has_syrupy = importlib.util.find_spec("syrupy") is not None

pytestmark = pytest.mark.skipif(not _has_syrupy, reason="syrupy not installed")

ALL_FIXTURES = discover_fixtures("")


def _api[T](cls: type[T], **attrs: Any) -> T:
    """Build an API instance without running __init__ (no network/session)."""
    api = cls.__new__(cls)
    for name, value in attrs.items():
        setattr(api, name, value)
    return api


def _parse(fixture_file: str) -> Vehicle:
    vehicle = Vehicle()
    data = load_fixture(fixture_file)

    if fixture_file.startswith("us_kia_"):
        _api(
            KiaUvoApiUSA,
            data_timezone=None,
            temperature_range=[62, 64, 66, 68, 70, 72, 74, 76, 78, 80, 82],
        )._update_vehicle_properties(vehicle, data)
    elif fixture_file.startswith("us_hyundai_"):
        _api(
            HyundaiBlueLinkApiUSA, data_timezone=None, temperature_range=range(62, 82)
        )._update_vehicle_properties(vehicle, data)
    elif fixture_file.startswith("eu_kia_gspa_"):
        _api(KiaCciApiEU)._update_vehicle_properties_ccs2(vehicle, data)
    elif fixture_file.startswith("eu_kia_") and "ccs2" in fixture_file:
        _api(
            ApiImplType1,
            data_timezone=None,
            temperature_range=[x * 0.5 for x in range(28, 60)],
        )._update_vehicle_properties_ccs2(vehicle, data)
    elif fixture_file.startswith("eu_kia_"):
        _api(
            KiaUvoApiEU,
            data_timezone=KiaUvoApiEU.data_timezone,
            temperature_range=KiaUvoApiEU.temperature_range,
        )._update_vehicle_properties(vehicle, data)
    elif fixture_file.startswith("br_"):
        _api(
            HyundaiBlueLinkApiBR, data_timezone=HyundaiBlueLinkApiBR.data_timezone
        )._update_vehicle_properties_ccs2(vehicle, data["resMsg"]["state"]["Vehicle"])
    elif fixture_file.startswith("ca_"):
        vehicle.year = 2022  # selects the CA temperature range
        _api(
            KiaUvoApiCA,
            data_timezone=KiaUvoApiCA.data_timezone,
            temperature_range_c_old=KiaUvoApiCA.temperature_range_c_old,
            temperature_range_c_new=KiaUvoApiCA.temperature_range_c_new,
            temperature_range_model_year=KiaUvoApiCA.temperature_range_model_year,
        )._update_vehicle_properties_base(vehicle, data)
    elif fixture_file.startswith("au_"):
        _api(
            KiaUvoApiAU,
            data_timezone=KiaUvoApiAU.data_timezone,
            temperature_range=KiaUvoApiAU.temperature_range,
        )._update_vehicle_properties(vehicle, data)
    elif fixture_file.startswith("cn_"):
        _api(
            KiaUvoApiCN,
            data_timezone=KiaUvoApiCN.data_timezone,
            temperature_range=KiaUvoApiCN.temperature_range,
        )._update_vehicle_properties(vehicle, data)
    else:
        pytest.fail(f"No parser mapped for fixture {fixture_file}")

    return vehicle


@pytest.mark.parametrize("fixture_file", ALL_FIXTURES, ids=ALL_FIXTURES)
def test_vehicle_snapshot(fixture_file: str, snapshot: SnapshotAssertion) -> None:
    vehicle = _parse(fixture_file)
    # vehicle.data is left out of the snapshot (it is the fixture itself).
    assert vehicle.data is not None
    assert vehicle_to_dict(vehicle) == snapshot
