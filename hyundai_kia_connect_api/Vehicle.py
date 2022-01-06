import logging

import dataclasses
import datetime
import re

from .const import *

_LOGGER = logging.getLogger(__name__)


@dataclasses.dataclass
class Vehicle:
    id: str = None
    name: str = None
    model: str = None
    registration_date: str = None
    year: int = None

    # Shared (EV/PHEV/HEV/IC)
    ## General 
    _total_driving_distance: float = None
    _total_driving_distance_value: float = None
    _total_driving_distance_unit: str = None

    _odometer: float = None
    _odometer_value: float = None
    _odometer_unit: str = None

    car_battery_percentage: int = None
    engine_is_running: bool = None
    last_updated_at: datetime.datetime = datetime.datetime.min
    ## Climate
    _air_temperature: float = None
    _air_temperature_value: float = None
    _air_temperature_unit: str = None

    defrost_is_on: bool = None
    steering_wheel_heater_is_on: bool = None
    back_window_heater_is_on: bool = None
    side_mirror_heater_is_on: bool = None
    front_left_seat_heater_is_on: bool = None
    front_right_seat_heater_is_on: bool = None
    rear_left_seat_heater_is_on: bool = None
    rear_right_seat_heater_is_on: bool = None
    ## Door Status
    is_locked: bool = None
    front_left_door_is_open: bool = None
    front_right_door_is_open: bool = None
    back_left_door_is_open: bool = None
    back_right_door_is_open: bool = None
    trunk_is_open: bool = None

    # EV fields (EV/PHEV)
    ev_battery_percentage: int = None
    ev_battery_is_charging: bool = None
    ev_battery_is_plugged_in: bool = None

    _ev_driving_distance: float = None
    _ev_driving_distance_value: float = None
    _ev_driving_distance_unit: str = None

    _ev_estimated_current_charge_duration: int = None
    _ev_estimated_current_charge_duration_value: int = None
    _ev_estimated_current_charge_duration_unit: str = None

    _ev_estimated_fast_charge_duration: int = None
    _ev_estimated_fast_charge_duration_value: int = None
    _ev_estimated_fast_charge_duration_unit: str = None

    _ev_estimated_portable_charge_duration: int = None
    _ev_estimated_portable_charge_duration_value: int = None
    _ev_estimated_portable_charge_duration_unit: str = None

    _ev_estimated_station_charge_duration: int = None
    _ev_estimated_station_charge_duration_value: int = None
    _ev_estimated_station_charge_duration_unit: str = None

    # IC fields (PHEV/HEV/IC)
    _fuel_driving_distance: float = None
    _fuel_driving_distance_value: float = None
    _fuel_driving_distance_unit: str = None

    fuel_level_is_low: bool = None

    # Calculated fields
    engine_type = None

    # Debug fields
    data: dict = None

    @property
    def total_driving_distance(self):
        return self._total_driving_distance

    @total_driving_distance.setter
    def total_driving_distance(self, value):
        self._total_driving_distance_value = value[0]
        self._total_driving_distance_unit = value[1]
        self._total_driving_distance = value[0]

    @property
    def odometer(self):
        return self._odometer

    @odometer.setter
    def odometer(self, value):
        self._odometer_value = value[0]
        self._odometer_unit = value[1]
        self._odometer = value[0]

    @property
    def air_temperature(self):
        return self._air_temperature

    @air_temperature.setter
    def air_temperature(self, value):
        self._air_temperature_value = value[0]
        self._air_temperature_unit = value[1]
        self._air_temperature = value[0]

    @property
    def ev_driving_distance(self):
        return self._ev_driving_distance

    @ev_driving_distance.setter
    def ev_driving_distance(self, value):
        self._ev_driving_distance_value = value[0]
        self._ev_driving_distance_unit = value[1]
        self._ev_driving_distance = value[0]

    @property
    def ev_estimated_current_charge_duration(self):
        return self._ev_estimated_current_charge_duration

    @ev_estimated_current_charge_duration.setter
    def ev_estimated_current_charge_duration(self, value):
        self._ev_estimated_current_charge_duration_value = value[0]
        self._ev_estimated_current_charge_duration_unit = value[1]
        self._ev_estimated_current_charge_duration = value[0]

    @property
    def ev_estimated_fast_charge_duration(self):
        return self._ev_estimated_fast_charge_duration

    @ev_estimated_fast_charge_duration.setter
    def ev_estimated_fast_charge_duration(self, value):
        self._ev_estimated_fast_charge_duration_value = value[0]
        self._ev_estimated_fast_charge_duration_unit = value[1]
        self._ev_estimated_fast_charge_duration = value[0]

    @property
    def ev_estimated_portable_charge_duration(self):
        return self._ev_estimated_portable_charge_duration

    @ev_estimated_portable_charge_duration.setter
    def ev_estimated_portable_charge_duration(self, value):
        self._ev_estimated_portable_charge_duration_value = value[0]
        self._ev_estimated_portable_charge_duration_unit = value[1]
        self._ev_estimated_portable_charge_duration = value[0]

    @property
    def ev_estimated_station_charge_duration(self):
        return self._ev_estimated_station_charge_duration

    @ev_estimated_station_charge_duration.setter
    def ev_estimated_station_charge_duration(self, value):
        self._ev_estimated_station_charge_duration_value = value[0]
        self._ev_estimated_station_charge_duration_unit = value[1]
        self._ev_estimated_station_charge_duration = value[0]

    @property
    def fuel_driving_distance(self):
        return self._fuel_driving_distance

    @fuel_driving_distance.setter
    def fuel_driving_distance(self, value):
        self._fuel_driving_distance_value = value[0]
        self._fuel_driving_distance_unit = value[1]
        self._fuel_driving_distance = value[0]
