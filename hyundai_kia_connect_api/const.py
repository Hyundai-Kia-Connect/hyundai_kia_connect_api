from dateutil import tz
from enum import Enum

DOMAIN: str = "kia_uvo"

BRAND_KIA = "Kia"
BRAND_HYUNDAI = "Hyundai"
BRANDS = {1: BRAND_KIA, 2: BRAND_HYUNDAI}

REGION_EUROPE = "Europe"
REGION_CANADA = "Canada"
REGION_USA = "USA"
REGIONS = {1: REGION_EUROPE, 2: REGION_CANADA, 3: REGION_USA}

DATE_FORMAT: str = "%Y-%m-%d %H:%M:%S.%f"

TIME_ZONE_EUROPE = tz.gettz("Europe/Berlin")

class VEHICLE_LOCK_ACTION(Enum):
    LOCK = "close"
    UNLOCK = "open"