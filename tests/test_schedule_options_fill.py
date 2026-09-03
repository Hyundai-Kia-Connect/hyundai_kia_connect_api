"""Tests for #1302: None = leave unchanged, scopes, auto-fill from Vehicle."""

import datetime as dt

from hyundai_kia_connect_api.ApiImpl import (
    ScheduleChargingClimateRequestOptions,
    _fill_schedule_options_from_vehicle,
    _schedule_charging_scopes,
)
from hyundai_kia_connect_api.Vehicle import Vehicle


def _vehicle(**kw) -> Vehicle:
    v = Vehicle()
    v.id = "vid-123"
    v.ev_first_departure_enabled = kw.get("first_departure_enabled", True)
    v.ev_first_departure_days = kw.get("first_departure_days", [1, 3])
    v.ev_first_departure_time = kw.get("first_departure_time", dt.time(7, 30))
    v.ev_second_departure_enabled = kw.get("second_departure_enabled", False)
    v.ev_second_departure_days = kw.get("second_departure_days", [0])
    v.ev_second_departure_time = kw.get("second_departure_time", dt.time(18, 0))
    v.ev_schedule_charge_enabled = kw.get("charging_enabled", True)
    v.ev_off_peak_start_time = kw.get("off_peak_start_time", dt.time(23, 0))
    v.ev_off_peak_end_time = kw.get("off_peak_end_time", dt.time(5, 0))
    v.ev_off_peak_charge_only_enabled = kw.get("off_peak_charge_only", True)
    v.ev_first_departure_climate_enabled = kw.get("climate_enabled", True)
    v.ev_first_departure_climate_temperature = (kw.get("temperature", 22.0), "°C")
    v.ev_first_departure_climate_defrost = kw.get("defrost", False)
    return v


class TestScopes:
    def test_charge_only(self):
        charge, climate = _schedule_charging_scopes(
            ScheduleChargingClimateRequestOptions(charging_enabled=True)
        )
        assert charge is True
        assert climate is False

    def test_climate_only_via_temperature(self):
        charge, climate = _schedule_charging_scopes(
            ScheduleChargingClimateRequestOptions(temperature=21.0)
        )
        assert charge is False
        assert climate is True

    def test_climate_only_via_departure(self):
        charge, climate = _schedule_charging_scopes(
            ScheduleChargingClimateRequestOptions(
                first_departure=ScheduleChargingClimateRequestOptions.DepartureOptions()
            )
        )
        assert charge is False
        assert climate is True

    def test_both_active(self):
        charge, climate = _schedule_charging_scopes(
            ScheduleChargingClimateRequestOptions(
                charging_enabled=False, climate_enabled=True
            )
        )
        assert charge is True
        assert climate is True

    def test_all_none(self):
        charge, climate = _schedule_charging_scopes(
            ScheduleChargingClimateRequestOptions()
        )
        assert charge is False
        assert climate is False


class TestFill:
    def test_fill_from_vehicle_maps_all_fields(self):
        options = ScheduleChargingClimateRequestOptions(charging_enabled=False)
        _fill_schedule_options_from_vehicle(
            options, _vehicle(), scopes=("charge", "climate")
        )
        assert options.charging_enabled is False  # explicit value preserved
        assert options.off_peak_start_time == dt.time(23, 0)
        assert options.off_peak_end_time == dt.time(5, 0)
        assert options.off_peak_charge_only_enabled is True
        assert options.first_departure.enabled is True
        assert options.first_departure.days == [1, 3]
        assert options.first_departure.time == dt.time(7, 30)
        assert options.second_departure.enabled is False
        assert options.climate_enabled is True
        assert options.temperature == 22.0
        assert options.temperature_unit == 0  # "°C" converted to 0
        assert options.defrost is False

    def test_fill_preserves_explicit_values(self):
        options = ScheduleChargingClimateRequestOptions(
            charging_enabled=False,
            off_peak_start_time=dt.time(1, 0),
            climate_enabled=False,
            temperature=24.0,
        )
        _fill_schedule_options_from_vehicle(
            options, _vehicle(), scopes=("charge", "climate")
        )
        assert options.charging_enabled is False
        assert options.off_peak_start_time == dt.time(1, 0)
        assert options.climate_enabled is False
        assert options.temperature == 24.0

    def test_fill_scoped_charge_only_skips_climate(self, caplog):
        """Climate scope not requested: no climate fill, no climate warnings."""
        options = ScheduleChargingClimateRequestOptions(charging_enabled=True)
        v = Vehicle()
        v.id = "vid-123"  # no climate state at all
        with caplog.at_level("WARNING"):
            _fill_schedule_options_from_vehicle(options, v, scopes=("charge",))
        assert options.climate_enabled is None
        assert options.temperature is None
        assert "climate_enabled" not in caplog.text

    def test_fill_unknown_state_defaults_and_warns(self, caplog):
        v = Vehicle()
        v.id = "vid-123"  # nothing reported
        options = ScheduleChargingClimateRequestOptions(charging_enabled=True)
        with caplog.at_level("WARNING"):
            _fill_schedule_options_from_vehicle(options, v, scopes=("charge",))
        assert options.charging_enabled is True  # explicit preserved
        assert options.off_peak_start_time == dt.time()  # default 00:00
        assert options.off_peak_end_time == dt.time()
        assert options.off_peak_charge_only_enabled is False
        assert "off_peak_start_time" in caplog.text
        assert "vid-123" in caplog.text

    def test_fill_partial_departure(self):
        """Provided departure with None fields is filled from vehicle state."""
        options = ScheduleChargingClimateRequestOptions(
            first_departure=ScheduleChargingClimateRequestOptions.DepartureOptions(
                enabled=False
            )
        )
        _fill_schedule_options_from_vehicle(options, _vehicle(), scopes=("climate",))
        assert options.first_departure.enabled is False  # preserved
        assert options.first_departure.days == [1, 3]
        assert options.first_departure.time == dt.time(7, 30)
        # None departure is created and filled
        assert options.second_departure.enabled is False
        assert options.second_departure.time == dt.time(18, 0)


class TestFromVehicle:
    def test_from_vehicle_builds_full_options(self):
        options = ScheduleChargingClimateRequestOptions.from_vehicle(_vehicle())
        assert options.charging_enabled is True
        assert options.first_departure.days == [1, 3]
        assert options.temperature_unit == 0
        assert options.temperature == 22.0

    def test_from_vehicle_unknown_state_defaults(self):
        options = ScheduleChargingClimateRequestOptions.from_vehicle(Vehicle())
        assert options.charging_enabled is False
        assert options.temperature == 21.0
        assert options.temperature_unit == 0
        assert options.first_departure.enabled is False
        assert options.first_departure.days == [0]
        assert options.first_departure.time == dt.time()
