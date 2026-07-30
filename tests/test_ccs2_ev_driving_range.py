"""Tests for ev_driving_range parsing in CCS2 (_update_vehicle_properties_ccs2).

PHEV vehicles report a separate electric-only range in ``DTE.EV`` (distinct
from ``DTE.Total``, which includes the ICE range). The CCS2 status parser set
``ev_driving_range`` only for pure EV (where it equals total_driving_range);
PHEV left it None. The fix reads ``Drivetrain.FuelSystem.DTE.EV`` for PHEV.

Confirmed against the EU Kia Sportage PHEV 2027 CCS2 fixture:
``DTE: {Total: 600, Unit: 1, EV: 47, ICE: 551}`` — 47 km electric-only range,
600 km total. HEV/ICE have no plug and no EV-only range, so ev_driving_range
stays None.
"""

import pytest

from hyundai_kia_connect_api.ApiImplType1 import ApiImplType1
from hyundai_kia_connect_api.const import ENGINE_TYPES
from hyundai_kia_connect_api.Vehicle import Vehicle
from tests.fixture_helpers import load_fixture

SPORTAGE_PHEV_FIXTURE = "eu_kia_sportage_phev_2027_ccs2.json"


@pytest.fixture
def ccs2_api() -> ApiImplType1:
    api = ApiImplType1.__new__(ApiImplType1)
    api.data_timezone = None
    api.temperature_range = [x * 0.5 for x in range(28, 60)]
    return api


def _parse(api, engine_type) -> Vehicle:
    vehicle = Vehicle()
    vehicle.engine_type = engine_type
    state = load_fixture(SPORTAGE_PHEV_FIXTURE)
    api._update_vehicle_properties_ccs2(vehicle, state)
    return vehicle


class TestEvDrivingRangePhev:
    def test_phev_ev_driving_range_from_dte_ev(self, ccs2_api):
        """PHEV: ev_driving_range is the electric-only DTE.EV, not DTE.Total."""
        vehicle = _parse(ccs2_api, ENGINE_TYPES.PHEV)
        assert vehicle.total_driving_range == 600.0
        assert vehicle.ev_driving_range == 47.0
        # Unit follows DTE.Unit (same as total_driving_range_unit).
        assert vehicle.ev_driving_range_unit == vehicle.total_driving_range_unit

    def test_phev_total_driving_range_still_dte_total(self, ccs2_api):
        """total_driving_range is unchanged: always DTE.Total."""
        vehicle = _parse(ccs2_api, ENGINE_TYPES.PHEV)
        assert vehicle.total_driving_range == 600.0

    def test_ice_ev_driving_range_stays_none(self, ccs2_api):
        """ICE/HEV have no EV-only range: ev_driving_range stays None even
        when the status (here a PHEV's) happens to carry a DTE.EV field."""
        vehicle = _parse(ccs2_api, ENGINE_TYPES.ICE)
        assert vehicle.total_driving_range == 600.0
        assert vehicle.ev_driving_range is None

    def test_ev_ev_driving_range_equals_total(self, ccs2_api):
        """Pure EV: ev_driving_range equals total_driving_range (unchanged)."""
        vehicle = _parse(ccs2_api, ENGINE_TYPES.EV)
        assert vehicle.ev_driving_range == 600.0
        assert vehicle.ev_driving_range == vehicle.total_driving_range
