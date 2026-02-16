"""Snapshot tests for the Vehicle object across all API implementations.

Uses syrupy to capture the full parsed Vehicle state for each fixture.
If any code change alters how a fixture is parsed into a Vehicle, the
snapshot diff will show exactly which fields changed.

To update snapshots after an intentional change::

    pytest tests/test_vehicle_snapshots.py --snapshot-update
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

import pytest

from hyundai_kia_connect_api.ApiImplType1 import ApiImplType1
from hyundai_kia_connect_api.HyundaiBlueLinkApiUSA import HyundaiBlueLinkApiUSA
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

# ---------------------------------------------------------------------------
# Fixture discovery (module-level so parametrize works)
# ---------------------------------------------------------------------------
US_KIA_FILES = discover_fixtures("us_kia_")
US_HYUNDAI_FILES = discover_fixtures("us_hyundai_")
EU_FILES = discover_fixtures("eu_kia_ev6_")
CCS2_FILES = discover_fixtures("eu_kia_ev9_")
CA_FILES = discover_fixtures("ca_")
AU_FILES = discover_fixtures("au_")
CN_FILES = discover_fixtures("cn_")


# ---------------------------------------------------------------------------
# API factory fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def usa_api() -> KiaUvoApiUSA:
    api = KiaUvoApiUSA.__new__(KiaUvoApiUSA)
    api.data_timezone = None
    api.temperature_range = [62, 64, 66, 68, 70, 72, 74, 76, 78, 80, 82]
    return api


@pytest.fixture
def bluelink_api() -> HyundaiBlueLinkApiUSA:
    api = HyundaiBlueLinkApiUSA.__new__(HyundaiBlueLinkApiUSA)
    api.data_timezone = None
    api.temperature_range = range(62, 82)
    return api


@pytest.fixture
def eu_api() -> KiaUvoApiEU:
    api = KiaUvoApiEU.__new__(KiaUvoApiEU)
    api.data_timezone = KiaUvoApiEU.data_timezone
    api.temperature_range = KiaUvoApiEU.temperature_range
    return api


@pytest.fixture
def ccs2_api() -> ApiImplType1:
    api = ApiImplType1.__new__(ApiImplType1)
    api.data_timezone = None
    api.temperature_range = [x * 0.5 for x in range(28, 60)]
    return api


@pytest.fixture
def ca_api() -> KiaUvoApiCA:
    api = KiaUvoApiCA.__new__(KiaUvoApiCA)
    api.data_timezone = KiaUvoApiCA.data_timezone
    api.temperature_range_c_old = KiaUvoApiCA.temperature_range_c_old
    api.temperature_range_c_new = KiaUvoApiCA.temperature_range_c_new
    api.temperature_range_model_year = KiaUvoApiCA.temperature_range_model_year
    return api


@pytest.fixture
def au_api() -> KiaUvoApiAU:
    api = KiaUvoApiAU.__new__(KiaUvoApiAU)
    api.data_timezone = KiaUvoApiAU.data_timezone
    api.temperature_range = KiaUvoApiAU.temperature_range
    return api


@pytest.fixture
def cn_api() -> KiaUvoApiCN:
    api = KiaUvoApiCN.__new__(KiaUvoApiCN)
    api.data_timezone = KiaUvoApiCN.data_timezone
    api.temperature_range = KiaUvoApiCN.temperature_range
    return api


# ---------------------------------------------------------------------------
# Snapshot tests — one test per region/API, parametrized over fixtures
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("fixture_file", US_KIA_FILES, ids=US_KIA_FILES)
def test_usa_kia_vehicle_snapshot(usa_api, fixture_file, snapshot: SnapshotAssertion):
    vehicle = Vehicle()
    data = load_fixture(fixture_file)
    usa_api._update_vehicle_properties(vehicle, data)
    assert vehicle_to_dict(vehicle) == snapshot


@pytest.mark.parametrize("fixture_file", US_HYUNDAI_FILES, ids=US_HYUNDAI_FILES)
def test_bluelink_usa_vehicle_snapshot(
    bluelink_api, fixture_file, snapshot: SnapshotAssertion
):
    vehicle = Vehicle()
    data = load_fixture(fixture_file)
    bluelink_api._update_vehicle_properties(vehicle, data)
    assert vehicle_to_dict(vehicle) == snapshot


@pytest.mark.parametrize("fixture_file", EU_FILES, ids=EU_FILES)
def test_eu_vehicle_snapshot(eu_api, fixture_file, snapshot: SnapshotAssertion):
    vehicle = Vehicle()
    data = load_fixture(fixture_file)
    eu_api._update_vehicle_properties(vehicle, data)
    assert vehicle_to_dict(vehicle) == snapshot


@pytest.mark.parametrize("fixture_file", CCS2_FILES, ids=CCS2_FILES)
def test_ccs2_vehicle_snapshot(ccs2_api, fixture_file, snapshot: SnapshotAssertion):
    vehicle = Vehicle()
    data = load_fixture(fixture_file)
    ccs2_api._update_vehicle_properties_ccs2(vehicle, data)
    assert vehicle_to_dict(vehicle) == snapshot


@pytest.mark.parametrize("fixture_file", CA_FILES, ids=CA_FILES)
def test_ca_vehicle_snapshot(ca_api, fixture_file, snapshot: SnapshotAssertion):
    vehicle = Vehicle()
    vehicle.year = 2022  # needed for temperature_range selection
    data = load_fixture(fixture_file)
    ca_api._update_vehicle_properties_base(vehicle, data)
    assert vehicle_to_dict(vehicle) == snapshot


@pytest.mark.parametrize("fixture_file", AU_FILES, ids=AU_FILES)
def test_au_vehicle_snapshot(au_api, fixture_file, snapshot: SnapshotAssertion):
    vehicle = Vehicle()
    data = load_fixture(fixture_file)
    au_api._update_vehicle_properties(vehicle, data)
    assert vehicle_to_dict(vehicle) == snapshot


@pytest.mark.parametrize("fixture_file", CN_FILES, ids=CN_FILES)
def test_cn_vehicle_snapshot(cn_api, fixture_file, snapshot: SnapshotAssertion):
    vehicle = Vehicle()
    data = load_fixture(fixture_file)
    cn_api._update_vehicle_properties(vehicle, data)
    assert vehicle_to_dict(vehicle) == snapshot
