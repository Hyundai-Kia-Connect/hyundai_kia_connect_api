"""last_updated_at fallback: prefer the vehicle-sourced time over the host clock.

When the status payload carries no timestamp (AU/IN/CN legacy /status/latest
key list has none; ccs2 payloads can lack `Date`), the parser used the HA
host clock, so the entity advanced at scan cadence (kia_uvo#1874, #1586).
The vehicle-sourced location time moves only when the car has reported; the
host clock is the last-resort fallback.
"""

import datetime as dt
from zoneinfo import ZoneInfo

from hyundai_kia_connect_api.ApiImplType1 import ApiImplType1
from hyundai_kia_connect_api.Vehicle import Vehicle


def _legacy_api() -> ApiImplType1:
    api = ApiImplType1.__new__(ApiImplType1)
    api.data_timezone = ZoneInfo("UTC")
    api.temperature_range = [x * 0.5 for x in range(28, 60)]
    return api


def test_legacy_prefers_location_time_when_status_has_none() -> None:
    api = _legacy_api()
    vehicle = Vehicle(id="VEHICLE-1")
    state = {
        "status": {"battery": {"Level": 82}},
        "vehicleLocation": {
            "coord": {"lat": 51.0, "lon": 19.0},
            "time": "20261101131728",
        },
    }
    api._update_vehicle_properties(vehicle, state)
    assert vehicle.last_updated_at == dt.datetime(
        2026, 11, 1, 13, 17, 28, tzinfo=dt.UTC
    )
    assert vehicle.location[1] == 51.0


def test_legacy_keeps_status_time_when_present() -> None:
    api = _legacy_api()
    vehicle = Vehicle(id="VEHICLE-1")
    state = {
        "status": {"time": "20261102091500", "battery": {"Level": 82}},
        "vehicleLocation": {
            "coord": {"lat": 51.0, "lon": 19.0},
            "time": "20261101131728",
        },
    }
    api._update_vehicle_properties(vehicle, state)
    assert vehicle.last_updated_at == dt.datetime(2026, 11, 2, 9, 15, 0, tzinfo=dt.UTC)


def test_ccs2_prefers_location_timestamp_when_date_absent() -> None:
    api = _legacy_api()
    vehicle = Vehicle(id="VEHICLE-1")
    state = {
        "Location": {
            "GeoCoord": {"Latitude": 51.0, "Longitude": 19.0},
            "TimeStamp": {
                "Year": 2026,
                "Mon": 11,
                "Day": 1,
                "Hour": 13,
                "Min": 17,
                "Sec": 28,
            },
        },
        "Drivetrain": {"FuelSystem": {"DTE": {"Total": 100, "Unit": 1}}},
    }
    api._update_vehicle_properties_ccs2(vehicle, state)
    assert vehicle.last_updated_at == dt.datetime(
        2026, 11, 1, 13, 17, 28, tzinfo=dt.UTC
    )


def test_ccs2_keeps_date_when_present() -> None:
    api = _legacy_api()
    vehicle = Vehicle(id="VEHICLE-1")
    state = {
        "Date": "20261105210000",
        "Location": {
            "GeoCoord": {"Latitude": 51.0, "Longitude": 19.0},
            "TimeStamp": {
                "Year": 2026,
                "Mon": 11,
                "Day": 1,
                "Hour": 13,
                "Min": 17,
                "Sec": 28,
            },
        },
        "Drivetrain": {"FuelSystem": {"DTE": {"Total": 100, "Unit": 1}}},
    }
    api._update_vehicle_properties_ccs2(vehicle, state)
    # The ccs2 `Date` is UTC on the wire; the fallback must not override a
    # present `Date`, only the timezone conversion applies.
    assert vehicle.last_updated_at == dt.datetime(2026, 11, 5, 21, 0, tzinfo=dt.UTC)
