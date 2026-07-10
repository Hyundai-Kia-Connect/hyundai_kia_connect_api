"""const.py"""

# pylint:disable=invalid-name,missing-class-docstring

import datetime
from enum import Enum, IntEnum

DOMAIN: str = "hyundai_kia_connect_api"

BRAND_KIA = "Kia"
BRAND_HYUNDAI = "Hyundai"
BRAND_GENESIS = "Genesis"
BRANDS = {1: BRAND_KIA, 2: BRAND_HYUNDAI, 3: BRAND_GENESIS}

GOOGLE = "google"
OPENSTREETMAP = "openstreetmap"
GEO_LOCATION_PROVIDERS = {1: OPENSTREETMAP, 2: GOOGLE}

REGION_EUROPE = "Europe"
REGION_CANADA = "Canada"
REGION_USA = "USA"
REGION_CHINA = "China"
REGION_AUSTRALIA = "Australia"
REGION_NZ = "New Zealand"
REGION_INDIA = "India"
REGION_BRAZIL = "Brazil"

REGIONS = {
    1: REGION_EUROPE,
    2: REGION_CANADA,
    3: REGION_USA,
    4: REGION_CHINA,
    5: REGION_AUSTRALIA,
    6: REGION_INDIA,
    7: REGION_NZ,
    8: REGION_BRAZIL,
}

LOGIN_TOKEN_LIFETIME = datetime.timedelta(hours=23)

LENGTH_KILOMETERS = "km"
LENGTH_MILES = "mi"
DISTANCE_UNITS = {
    None: None,
    0: None,
    1: LENGTH_KILOMETERS,
    2: LENGTH_MILES,
    3: LENGTH_MILES,
}

TEMPERATURE_C = "°C"
TEMPERATURE_F = "°F"
TEMPERATURE_UNITS = {None: None, 0: TEMPERATURE_C, 1: TEMPERATURE_F}

PRESSURE_KPA = "kPa"
PRESSURE_BAR = "bar"
PRESSURE_PSI = "psi"


class PressureUnit(IntEnum):
    """Tire-pressure display unit (Chassis.Axle.Tire.PressureUnit, CCS2).

    The car's tire-pressure display unit is user-selectable (psi | kPa | bar);
    the enum maps to consecutive ints matching the car's selector order.
    """

    PSI = 0
    KPA = 1
    BAR = 2


# Model B (live-confirmed EU Santa Fe 2026): raw Pressure is in the car's
# display unit, and the scale depends on the unit:
#   bar (PressureUnit.BAR): raw 27 -> 2.7  (x0.1, 0.1-bar steps)
#   psi (PressureUnit.PSI): raw 38 -> 38   (x1, integer psi)
#   kPa (PressureUnit.KPA): raw 51 -> 255   (x5, 5-kPa steps) — live-confirmed.
PRESSURE_UNITS = {
    PressureUnit.PSI: PRESSURE_PSI,
    PressureUnit.KPA: PRESSURE_KPA,
    PressureUnit.BAR: PRESSURE_BAR,
}
# Raw Pressure -> display-value scale per PressureUnit (model B).
PRESSURE_SCALES = {
    PressureUnit.PSI: 1.0,
    PressureUnit.KPA: 5.0,
    PressureUnit.BAR: 0.1,
}

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


class ORDER_STATUS(Enum):
    # pending (waiting for response from vehicle)
    PENDING = "PENDING"
    # order executed by vehicle and response returned
    SUCCESS = "SUCCESS"
    # order refused by vehicle and response returned
    FAILED = "FAILED"
    # no response received from vehicle.
    # no way to know if the order was executed, but most likely not
    TIMEOUT = "TIMEOUT"
    # Used when we don't know the status of the order
    UNKNOWN = "UNKNOWN"


class WINDOW_STATE(IntEnum):
    CLOSED = 0
    OPEN = 1
    VENTILATION = 2


class VALET_MODE_ACTION(Enum):
    ACTIVATE = "activate"
    DEACTIVATE = "deactivate"


class OTP_NOTIFY_TYPE(Enum):
    EMAIL = "EMAIL"
    SMS = "SMS"
