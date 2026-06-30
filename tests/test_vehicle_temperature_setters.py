"""Tests for Vehicle air_temperature / outside_temperature setter coercion."""

from hyundai_kia_connect_api.Vehicle import Vehicle


def test_air_temperature_numeric_string_becomes_float():
    vehicle = Vehicle()
    vehicle.air_temperature = ("72", "°F")
    assert vehicle.air_temperature == 72.0
    assert isinstance(vehicle.air_temperature, float)


def test_air_temperature_value_backing_stays_raw():
    vehicle = Vehicle()
    vehicle.air_temperature = ("72", "°F")
    assert vehicle._air_temperature_value == "72"
    assert vehicle._air_temperature_unit == "°F"


def test_air_temperature_off_becomes_none():
    vehicle = Vehicle()
    vehicle.air_temperature = ("OFF", "°C")
    assert vehicle.air_temperature is None
    # raw diagnostic value is preserved
    assert vehicle._air_temperature_value == "OFF"


def test_air_temperature_non_numeric_becomes_none():
    vehicle = Vehicle()
    vehicle.air_temperature = ("abc", "°C")
    assert vehicle.air_temperature is None
    assert vehicle._air_temperature_value == "abc"


def test_air_temperature_none_becomes_none():
    vehicle = Vehicle()
    vehicle.air_temperature = (None, "°C")
    assert vehicle.air_temperature is None


def test_outside_temperature_numeric_string_becomes_float():
    vehicle = Vehicle()
    vehicle.outside_temperature = ("21", "°C")
    assert vehicle.outside_temperature == 21.0
    assert isinstance(vehicle.outside_temperature, float)


def test_outside_temperature_value_backing_stays_raw():
    vehicle = Vehicle()
    vehicle.outside_temperature = ("21", "°C")
    assert vehicle._outside_temperature_value == "21"
    assert vehicle._outside_temperature_unit == "°C"


def test_outside_temperature_non_numeric_becomes_none():
    vehicle = Vehicle()
    vehicle.outside_temperature = ("n/a", "°C")
    assert vehicle.outside_temperature is None
    assert vehicle._outside_temperature_value == "n/a"
