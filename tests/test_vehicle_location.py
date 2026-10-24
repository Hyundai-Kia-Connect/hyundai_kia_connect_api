import datetime as dt

import pytest

from hyundai_kia_connect_api import Vehicle

WHEN = dt.datetime(2026, 1, 2, 3, 4, 5, tzinfo=dt.UTC)


@pytest.mark.parametrize(
    ("lat", "lon", "expected"),
    [
        (51.5, -0.12, (51.5, -0.12)),
        ("51.5", "-0.12", (51.5, -0.12)),
        (51, 0, (51.0, 0.0)),
        (None, None, (None, None)),
        ("", "n/a", (None, None)),
    ],
)
def test_location_is_stored_as_float(lat, lon, expected) -> None:
    vehicle = Vehicle()
    vehicle.location = (lat, lon, WHEN)
    assert (vehicle.location_latitude, vehicle.location_longitude) == expected
    for value in (vehicle.location_latitude, vehicle.location_longitude):
        assert value is None or type(value) is float
