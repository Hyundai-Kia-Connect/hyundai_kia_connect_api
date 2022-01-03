import logging

import dataclasses
import datetime
import re
import traceback

from .const import *
from .utils import get_child_value

_LOGGER = logging.getLogger(__name__)


@dataclasses.dataclass
class Vehicle:
    id: str = None
    name: str = None
    model: str = None
    year: int = None
    registration_date: str = None
    region: int = None
    brand: int = None
    data_timezone: datetime.tzinfo = None

    # Shared (EV/PHEV/HEV/IC)
    ## General
    _total_driving_distance: float = None
    _odometer: float = None
    _car_battery_percentage: int = None
    _engine_is_running: bool = None
    _last_updated_at: datetime.datetime = datetime.datetime.min
    ## Climate
    _air_temperature: float = None
    _defrost_is_on: bool = None
    _steering_wheel_heater_is_on: bool = None
    _back_window_heater_is_on: bool = None
    _side_mirror_heater_is_on: bool = None
    _front_left_heater_is_on: bool = None
    _front_right_heater_is_on: bool = None
    _rear_left_heater_is_on: bool = None
    _rear_right_heater_is_on: bool = None
    ## Door Status
    _is_locked: bool = None
    _front_left_door_is_open: bool = None
    _front_right_door_is_open: bool = None
    _back_left_door_is_open: bool = None
    _back_right_door_is_open: bool = None
    _trunk_is_open: bool = None

    # EV fields (EV/PHEV)
    _ev_battery_percentage: int = None
    _ev_battery_is_charging: bool = None
    _ev_battery_is_charging: bool = None
    _ev_driving_distance: float = None
    _ev_estimated_current_charge_duration: int = None
    _ev_estimated_fast_charge_duration: int = None
    _ev_estimated_portable_charge_duration: int = None
    _ev_estimated_station_charge_duration: int = None

    # IC fields (PHEV/HEV/IC)
    _fuel_driving_distance: float = None
    _fuel_level_is_low: bool = None

    # Calculated fields
    _engine_type = None

    @property
    def total_driving_distance(self):
        return self._total_driving_distance

    @total_driving_distance.setter
    def total_driving_distance(self, value):
        self._total_driving_distance = value

    @property
    def odometer(self):
        return self._odometer

    @odometer.setter
    def odometer(self, value):
        self._odometer = value

    @property
    def car_battery_percentage(self):
        return self._car_battery_percentage

    @car_battery_percentage.setter
    def car_battery_percentage(self, value):
        self._car_battery_percentage = value

    @property
    def engine_is_running(self):
        return self._engine_is_running

    @engine_is_running.setter
    def engine_is_running(self, value):
        self._engine_is_running = value

    @property
    def last_updated_at(self):
        return self._last_updated_at

    @last_updated_at.setter
    def last_updated_at(self, value):
        m = re.match(r"(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})", value)
        _LOGGER.debug(f"{DOMAIN} - last_updated_at {value}")
        value = datetime.datetime(
            year=int(m.group(1)),
            month=int(m.group(2)),
            day=int(m.group(3)),
            hour=int(m.group(4)),
            minute=int(m.group(5)),
            second=int(m.group(6)),
            tzinfo=self.data_timezone,
        )
        self._last_updated_at = value

    def set_state(self, state, data_map):
        for key in data_map.keys():
            setattr(self, key.fset.__name__, get_child_value(state, data_map[key]))
        # self.odometer = get_child_value(state, "odometer.value")
        # self.last_updated_at(get_child_value(state, "vehicleStatus.time"))
