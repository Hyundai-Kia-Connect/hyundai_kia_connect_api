"""const.py"""
# pylint:disable=invalid-name,missing-class-docstring

from enum import Enum

DOMAIN: str = "hyundai_kia_connect_api"

BRAND_KIA = "Kia"
BRAND_HYUNDAI = "Hyundai"
BRANDS = {1: BRAND_KIA, 2: BRAND_HYUNDAI}

REGION_EUROPE = "Europe"
REGION_CANADA = "Canada"
REGION_USA = "USA"
REGIONS = {1: REGION_EUROPE, 2: REGION_CANADA, 3: REGION_USA}

LENGTH_KILOMETERS = "km"
LENGTH_MILES = "mi"
DISTANCE_UNITS = {None: None, 0: None, 1: LENGTH_KILOMETERS, 3: LENGTH_MILES}

TEMPERATURE_C = "°C"
TEMPERATURE_F = "°F"
TEMPERATURE_UNITS = {None: None, 0: TEMPERATURE_C, 1: TEMPERATURE_F}

SEAT_STATUS = {
    None: None,
    0: "Off",
    1: "On",
    2: "Off",
    3: "Low Cool",
    4: "Medium Cool",
    5: "High Cool",
    6: "Low Heat",
    7: "Medium Heat",
    8: "High Heat",
}

HEAT_STATUS = {
    None: None,
    0: "Off",
    1: "Steering Wheel and Rear Window",
    2: "Rear Window",
    3: "Steering Wheel",
    # Seems to be the same as 1 but different region (EU):
    4: "Steering Wheel and Rear Window",
}


class ENGINE_TYPES(Enum):
    ICE = "ICE"
    EV = "EV"
    PHEV = "PHEV"
    HEV = "HEV"


class VEHICLE_LOCK_ACTION(Enum):
    LOCK = "close"
    UNLOCK = "open"


class CHARGE_PORT_ACTION(Enum):
    CLOSE = "close"
    OPEN = "open"


class OrderStatus(Enum):
    # pending (waiting for response from vehicle)
    PENDING = "PENDING"
    # order executed by vehicle and response returned
    SUCCESS = "SUCCESS"
    # order refused by vehicle and response returned
    FAILED = "FAILED"
    # no response received from vehicle.
    # no way to know if the order was executed, but most likely not
    TIMEOUT = "TIMEOUT"
