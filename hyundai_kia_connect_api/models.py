"""Data models for the Hyundai and Kia Connect API."""

from dataclasses import dataclass, field
import datetime as dt
from enum import IntEnum


@dataclass
class CachedVehicleState:
    location: "VehicleLocation" | None = None
    details: dict = field(default_factory=dict)
    current_state: dict = field(default_factory=dict)


@dataclass
class TripInfo:
    trip_day_list: list["TripDayListItem"]
    trip_period_type: "TripPeriodType"
    month_trip_day_cnt: int
    trip_drv_time: int
    trip_idle_time: int
    trip_dist: int
    trip_avg_speed: float
    trip_max_speed: float


@dataclass
class TripDayListItem:
    date: dt.datetime
    count: int


@dataclass
class VehicleLocation:
    lat: float
    long: float
    time: dt.datetime


class TripPeriodType(IntEnum):
    MONTH = 0
    DAY = 1
