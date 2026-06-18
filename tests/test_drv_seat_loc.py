"""Tests for CCS2 drvSeatLoc resolution (LHD vs RHD).

`start_climate` on the CCS2 path sends a `drvSeatLoc` field ("L" or "R") that
tells the car which side the driver seat is on. Previously this was hardcoded
to "R", which is wrong for the LHD majority of EU/CN. It is now resolved per
region:

- Base ``ApiImplType1._get_drv_seat_loc`` uses the vehicle's odometer unit:
  mile-based markets (UK, Ireland) -> "R", kilometre-based -> "L".
- ``KiaUvoApiAU`` and ``KiaUvoApiIN`` override to always return "R" because
  AU/IN are RHD despite using kilometres.
- EU and CN use the base implementation.

Mirrors egmp-bluelink-scriptable: ``europe.ts`` uses
``distanceUnit === 'mi' ? 'R' : 'L'`` while ``australia.ts`` hardcodes
``drvSeatLoc: 'R'``.
"""

import pytest

from hyundai_kia_connect_api.ApiImplType1 import ApiImplType1
from hyundai_kia_connect_api.KiaUvoApiAU import KiaUvoApiAU
from hyundai_kia_connect_api.KiaUvoApiIN import KiaUvoApiIN
from hyundai_kia_connect_api.const import DISTANCE_UNITS
from hyundai_kia_connect_api.Vehicle import Vehicle


@pytest.fixture
def vehicle():
    v = Vehicle()
    v.id = "test-vehicle-id"
    return v


def _set_km(v: Vehicle) -> Vehicle:
    v._odometer_unit = DISTANCE_UNITS[1]  # "km"
    return v


def _set_miles(v: Vehicle) -> Vehicle:
    v._odometer_unit = DISTANCE_UNITS[2]  # "mi"
    return v


def _set_unit_none(v: Vehicle) -> Vehicle:
    v._odometer_unit = None
    return v


class TestBaseDrvSeatLoc:
    """Base ApiImplType1 (used by EU and CN)."""

    def setup_method(self):
        self.api = ApiImplType1()

    def test_kilometres_resolves_to_lhd(self, vehicle):
        assert self.api._get_drv_seat_loc(_set_km(vehicle)) == "L"

    def test_miles_resolves_to_rhd(self, vehicle):
        assert self.api._get_drv_seat_loc(_set_miles(vehicle)) == "R"

    def test_miles_unit_3_also_resolves_to_rhd(self, vehicle):
        vehicle._odometer_unit = DISTANCE_UNITS[3]  # "mi"
        assert self.api._get_drv_seat_loc(vehicle) == "R"

    def test_missing_unit_falls_back_to_lhd(self, vehicle):
        """odometer_unit can be None when the cached state response omits it.

        LHD is the correct fallback for the EU/CN majority.
        """
        assert self.api._get_drv_seat_loc(_set_unit_none(vehicle)) == "L"


class TestAUDrvSeatLoc:
    """AU is RHD despite using kilometres — must always return "R"."""

    def setup_method(self):
        # region=5 (Australia), brand=2 (Hyundai)
        self.api = KiaUvoApiAU(region=5, brand=2, language="en")

    def test_au_kilometres_still_rhd(self, vehicle):
        assert self.api._get_drv_seat_loc(_set_km(vehicle)) == "R"

    def test_au_miles_still_rhd(self, vehicle):
        assert self.api._get_drv_seat_loc(_set_miles(vehicle)) == "R"

    def test_au_missing_unit_still_rhd(self, vehicle):
        assert self.api._get_drv_seat_loc(_set_unit_none(vehicle)) == "R"


class TestINDrvSeatLoc:
    """IN is RHD despite using kilometres — must always return "R"."""

    def setup_method(self):
        # brand=2 (Hyundai)
        self.api = KiaUvoApiIN(brand=2)

    def test_in_kilometres_still_rhd(self, vehicle):
        assert self.api._get_drv_seat_loc(_set_km(vehicle)) == "R"

    def test_in_miles_still_rhd(self, vehicle):
        assert self.api._get_drv_seat_loc(_set_miles(vehicle)) == "R"

    def test_in_missing_unit_still_rhd(self, vehicle):
        assert self.api._get_drv_seat_loc(_set_unit_none(vehicle)) == "R"
