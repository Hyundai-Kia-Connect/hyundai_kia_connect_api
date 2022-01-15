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
DISTANCE_UNITS = {None: None, 1: LENGTH_KILOMETERS, 3: LENGTH_MILES}

TEMPERATURE_C = "c"
TEMPERATURE_F = "f"
TEMPERATURE_UNITS = {None: None, 0: TEMPERATURE_C, 1: TEMPERATURE_F}

SEAT_STATUS = {
    0: "Off",
    1: "On",
    2: "Off",
    3: "Low Cool",
    4: "Medium Cool",
    5: "Full Cool",
    6: "Low Heat",
    7: "Medium Heat",
    8: "High Heat",
}


class VEHICLE_LOCK_ACTION(Enum):
    LOCK = "close"
    UNLOCK = "open"
