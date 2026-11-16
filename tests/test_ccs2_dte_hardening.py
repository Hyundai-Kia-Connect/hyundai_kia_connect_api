"""Tests for DTE distance hardening in the EU CCS2 parsers (#1606).

A EU backend change started shipping an unmapped DTE unit code (70) in
``Drivetrain.FuelSystem.DTE.Unit``; the unguarded ``DISTANCE_UNITS[...]``
lookups raised KeyError and aborted the whole vehicle update, leaving the
kia_uvo integration in setup_retry.

The vendor apps never crash on unknown units: every generation maps
unmapped codes to km (OneApp EU 1.5.x ``DistanceUnit.convert1Km2Mile`` =
1->Km, 2->Mile, else->Km; BlueLink 2.0.26 ``f.a`` = unit 3 -> mi, else ->
km). We deviate on purpose: an unmapped code means the distance cannot be
interpreted, so ranges become unknown instead of a guessed unit.

The 12-bit DTE sentinels (4095 for total/ICE, 1023 for EV-only) are
treated as invalid readings by the same apps (zeroed for display, "--"
in BlueLink 2.0.26); the library turned them into real ranges. A
sentinel here becomes unknown too: a fake "0 km" range is worse for
automations than a missing one.
"""

import logging

from hyundai_kia_connect_api.const import (
    DISTANCE_UNITS,
    LENGTH_KILOMETERS,
    LENGTH_MILES,
)
from hyundai_kia_connect_api.utils import parse_distance_unit, refine_dte_distance


class TestParseDistanceUnit:
    def test_known_codes(self):
        assert parse_distance_unit(1) == LENGTH_KILOMETERS
        assert parse_distance_unit(2) == LENGTH_MILES
        assert parse_distance_unit(3) == LENGTH_MILES

    def test_unspecified_codes_silent(self, caplog):
        with caplog.at_level(logging.WARNING):
            assert parse_distance_unit(None) is None
            assert parse_distance_unit(0) is None
        assert "Unknown distance unit" not in caplog.text
        assert DISTANCE_UNITS[None] is None and DISTANCE_UNITS[0] is None

    def test_unknown_code_70(self, caplog):
        assert parse_distance_unit(70) is None
        assert "70" in caplog.text

    def test_unknown_code_string(self, caplog):
        assert parse_distance_unit("kg") is None
        assert "kg" in caplog.text


class TestRefineDteDistance:
    def test_passes_through(self):
        assert refine_dte_distance(450) == 450.0
        assert refine_dte_distance("450") == 450.0

    def test_zero_is_real(self):
        assert refine_dte_distance(0) == 0.0

    def test_total_ice_sentinel_4095(self, caplog):
        assert refine_dte_distance(4095) is None
        assert "4095" in caplog.text

    def test_ev_sentinel_1023_only_ev_slot(self, caplog):
        assert refine_dte_distance(1023) == 1023.0
        assert refine_dte_distance(1023, ev=True) is None
        assert "1023" in caplog.text

    def test_out_of_domain(self, caplog):
        with caplog.at_level(logging.WARNING):
            assert refine_dte_distance(-1) is None
            assert refine_dte_distance(4094.5) is None
            assert refine_dte_distance(6000) is None
        assert "sentinel" not in caplog.text

    def test_none(self):
        assert refine_dte_distance(None) is None
