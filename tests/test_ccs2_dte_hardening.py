"""Tests for DTE distance hardening in the EU CCS2 parsers (#1606).

An unmapped DTE unit code (70) started arriving from the EU backend;
the bare DISTANCE_UNITS lookups raised KeyError and aborted the whole
vehicle update, leaving the kia_uvo integration in setup_retry.

Every vendor app generation treats unmapped units as km and zeroes the
12-bit DTE sentinels (4095 total/ICE, 1023 EV) for display. The parsers
instead report an unmapped unit or a sentinel as unknown: a guessed
unit or a fake 0 km range is worse for automations than a missing one.
"""

import logging

import pytest

from hyundai_kia_connect_api.ApiImplType1 import ApiImplType1
from hyundai_kia_connect_api.const import (
    DISTANCE_UNITS,
    ENGINE_TYPES,
    LENGTH_KILOMETERS,
    LENGTH_MILES,
)
from hyundai_kia_connect_api.GspaApiEU import GspaApiEU
from hyundai_kia_connect_api.utils import parse_distance_unit, refine_dte_distance
from hyundai_kia_connect_api.Vehicle import Vehicle


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


EV_STATE = {
    "Drivetrain": {"FuelSystem": {"DTE": {"Unit": 1, "Total": 450}}},
    "Electronics": {"Battery": {"Level": 62}},
    "Green": {
        "ChargingInformation": {"DTE": {"TargetSoC": {"Standard": 300, "Quick": 320}}}
    },
}

PHEV_STATE = {
    "Drivetrain": {
        "FuelSystem": {"DTE": {"Unit": 1, "Total": 600, "EV": 47, "ICE": 551}}
    },
    "Electronics": {"Battery": {"Level": 62}},
}


def _dte_state(base, **dte_overrides):
    """Deep-copied status with Drivetrain.FuelSystem.DTE overrides."""
    import copy

    state = copy.deepcopy(base)
    state["Drivetrain"]["FuelSystem"]["DTE"].update(dte_overrides)
    return state


class TestApiImplType1Dte:
    @pytest.fixture
    def api(self):
        api = ApiImplType1.__new__(ApiImplType1)
        api.data_timezone = None
        api.temperature_range = [x * 0.5 for x in range(28, 60)]
        return api

    def _parse(self, api, state, engine_type=ENGINE_TYPES.EV):
        vehicle = Vehicle(id="test-id")
        vehicle.engine_type = engine_type
        api._update_vehicle_properties_ccs2(vehicle, state)
        return vehicle

    def test_unknown_unit_70_does_not_raise_and_clears_ranges(self, api, caplog):
        vehicle = self._parse(api, _dte_state(EV_STATE, Unit=70, Total=250))
        assert vehicle.total_driving_range is None
        assert vehicle.ev_target_range_charge_AC is None
        assert vehicle.ev_target_range_charge_DC is None
        assert vehicle.car_battery_percentage == 62
        assert "70" in caplog.text

    def test_unspecified_unit_0_clears_ranges_without_warning(self, api, caplog):
        with caplog.at_level(logging.WARNING):
            vehicle = self._parse(api, _dte_state(EV_STATE, Unit=0, Total=250))
        assert vehicle.total_driving_range is None
        assert "Unknown distance unit" not in caplog.text

    def test_valid_unit_keeps_ranges(self, api):
        vehicle = self._parse(api, EV_STATE)
        assert vehicle.total_driving_range == 450.0
        assert vehicle.total_driving_range_unit == LENGTH_KILOMETERS
        assert vehicle.ev_target_range_charge_AC == 300.0
        assert vehicle.ev_target_range_charge_DC == 320.0

    def test_valid_then_unknown_unit_clears_stale_ranges(self, api):
        keep = self._parse(api, EV_STATE)
        assert keep.total_driving_range == 450.0
        stale = self._parse(api, _dte_state(EV_STATE, Unit=999))
        assert stale.total_driving_range is None
        assert stale.ev_target_range_charge_AC is None

    def test_phev_ev_sentinel_1023(self, api):
        vehicle = self._parse(
            api,
            _dte_state(PHEV_STATE, EV=1023),
            engine_type=ENGINE_TYPES.PHEV,
        )
        assert vehicle.total_driving_range == 600.0
        assert vehicle.ev_driving_range is None
        assert vehicle.fuel_driving_range == 551.0

    def test_phev_ice_sentinel_4095(self, api):
        vehicle = self._parse(
            api,
            _dte_state(PHEV_STATE, Total=4095, ICE=4095),
            engine_type=ENGINE_TYPES.PHEV,
        )
        assert vehicle.total_driving_range is None
        assert vehicle.fuel_driving_range is None
        assert vehicle.ev_driving_range is None

    def test_missing_total_no_longer_raises(self, api):
        state = _dte_state(EV_STATE)
        del state["Drivetrain"]["FuelSystem"]["DTE"]["Total"]
        vehicle = self._parse(api, state)
        assert vehicle.total_driving_range is None


GSPA_STATE = {
    "Drivetrain": {"FuelSystem": {"DTE": {"Unit": 1, "Total": 450, "Fuel": 90}}},
    "Electronics": {"Battery": {"Level": 62}},
    "Green": {
        "ChargingInformation": {"DTE": {"TargetSoC": {"Standard": 300, "Quick": 320}}}
    },
}


class TestGspaDte:
    @pytest.fixture
    def api(self):
        import datetime

        api = GspaApiEU.__new__(GspaApiEU)
        api.data_timezone = datetime.UTC
        return api

    def _parse(self, api, state, engine_type=ENGINE_TYPES.EV):
        vehicle = Vehicle(id="test-id")
        vehicle.engine_type = engine_type
        api._update_vehicle_properties_ccs2(vehicle, state)
        return vehicle

    def test_gspa_unknown_unit_70_does_not_raise_and_clears_ranges(self, api, caplog):
        vehicle = self._parse(api, _dte_state(GSPA_STATE, Unit=70, Total=250))
        assert vehicle.total_driving_range is None
        assert vehicle.ev_target_range_charge_AC is None
        assert vehicle.ev_target_range_charge_DC is None
        assert vehicle.car_battery_percentage == 62
        assert "70" in caplog.text

    def test_gspa_valid_unit_1_keeps_ranges(self, api):
        vehicle = self._parse(api, GSPA_STATE)
        assert vehicle.total_driving_range == 450.0
        assert vehicle.total_driving_range_unit == LENGTH_KILOMETERS
        assert vehicle.fuel_driving_range == 90.0

    def test_gspa_total_sentinel_4095(self, api):
        vehicle = self._parse(api, _dte_state(GSPA_STATE, Total=4095))
        assert vehicle.total_driving_range is None
        assert vehicle.fuel_driving_range == 90.0

    def test_gspa_valid_then_unknown_clears_stale(self, api):
        keep = self._parse(api, GSPA_STATE)
        assert keep.total_driving_range == 450.0
        stale = self._parse(api, _dte_state(GSPA_STATE, Unit=999))
        assert stale.total_driving_range is None
        assert stale.ev_target_range_charge_AC is None


class TestGspaDteSentinel:
    @pytest.fixture
    def api(self):
        import datetime

        api = GspaApiEU.__new__(GspaApiEU)
        api.data_timezone = datetime.UTC
        return api

    @pytest.fixture
    def vehicle(self):
        vehicle = Vehicle(id="test-id")
        vehicle.engine_type = ENGINE_TYPES.EV
        return vehicle

    def test_gspa_fuel_sentinel_4095_clears_stale(self, api, vehicle):
        api._update_vehicle_properties_ccs2(vehicle, _dte_state(GSPA_STATE, Fuel=550))
        assert vehicle.fuel_driving_range == 550.0
        api._update_vehicle_properties_ccs2(vehicle, _dte_state(GSPA_STATE, Fuel=4095))
        assert vehicle.fuel_driving_range is None

    def test_gspa_missing_fuel_clears(self, api, vehicle):
        api._update_vehicle_properties_ccs2(vehicle, GSPA_STATE)
        assert vehicle.fuel_driving_range == 90.0
        api._update_vehicle_properties_ccs2(vehicle, _dte_state(GSPA_STATE, Fuel=None))
        assert vehicle.fuel_driving_range is None


class TestPhevMissingTotal:
    @pytest.fixture
    def api(self):
        api = ApiImplType1.__new__(ApiImplType1)
        api.data_timezone = None
        api.temperature_range = [x * 0.5 for x in range(28, 60)]
        return api

    def test_phev_missing_total_clears_ev_and_fuel(self, api):
        vehicle = Vehicle(id="test-id")
        vehicle.engine_type = ENGINE_TYPES.PHEV
        state = _dte_state(PHEV_STATE)
        del state["Drivetrain"]["FuelSystem"]["DTE"]["Total"]
        api._update_vehicle_properties_ccs2(vehicle, state)
        assert vehicle.total_driving_range is None
        assert vehicle.ev_driving_range is None
        assert vehicle.fuel_driving_range is None
