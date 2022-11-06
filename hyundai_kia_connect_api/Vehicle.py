import logging

import dataclasses
import datetime
import re

import pytz

from .const import *

_LOGGER = logging.getLogger(__name__)

@dataclasses.dataclass
class EvChargeLimits:
    ac: int = None
    dc: int = None

@dataclasses.dataclass
class Vehicle:
    id: str = None
    name: str = None
    model: str = None
    registration_date: str = None
    year: int = None
    VIN: str = None
    key: str = None

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

    smart_key_battery_warning_is_on: bool = None
    washer_fluid_warning_is_on: bool = None
    brake_fluid_warning_is_on: bool = None

    ## Climate
    _air_temperature: float = None
    _air_temperature_value: float = None
    _air_temperature_unit: str = None

    air_control_is_on: bool = None
    defrost_is_on: bool = None
    steering_wheel_heater_is_on: bool = None
    back_window_heater_is_on: bool = None
    side_mirror_heater_is_on: bool = None
    front_left_seat_status: str = None
    front_right_seat_status: str = None
    rear_left_seat_status: str = None
    rear_right_seat_status: str = None
    
    ## Door Status
    is_locked: bool = None
    front_left_door_is_open: bool = None
    front_right_door_is_open: bool = None
    back_left_door_is_open: bool = None
    back_right_door_is_open: bool = None
    trunk_is_open: bool = None
    hood_is_open: bool = None

    # Tire Pressure
    tire_pressure_all_warning_is_on: bool = None
    tire_pressure_rear_left_warning_is_on: bool = None
    tire_pressure_front_left_warning_is_on: bool = None
    tire_pressure_front_right_warning_is_on: bool = None
    tire_pressure_rear_right_warning_is_on: bool = None

    # Service Data
    _next_service_distance: float = None
    _next_service_distance_value: float = None
    _next_service_distance_unit: str = None
    _last_service_distance: float = None
    _last_service_distance_value: float = None
    _last_service_distance_unit: str = None

    # Location
    _location_latitude: float = None
    _location_longitude: float = None
    _location_last_set_time: datetime.datetime = None

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

    _ev_charge_limits: EvChargeLimits = None

    # IC fields (PHEV/HEV/IC)
    _fuel_driving_distance: float = None
    _fuel_driving_distance_value: float = None
    _fuel_driving_distance_unit: str = None
    fuel_level: float = None


    fuel_level_is_low: bool = None

    # Calculated fields
    engine_type: str = None

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
    def next_service_distance(self):
        return self._next_service_distance

    @next_service_distance.setter
    def next_service_distance(self, value):
        self._next_service_distance_value = value[0]
        self._next_service_distance_unit = value[1]
        self._next_service_distance = value[0]

    @property
    def last_service_distance(self):
        return self._last_service_distance

    @last_service_distance.setter
    def last_service_distance(self, value):
        self._last_service_distance_value = value[0]
        self._last_service_distance_unit = value[1]
        self._last_service_distance = value[0]

    @property
    def location_latitude(self):
        return self._location_latitude

    @property
    def location_longitude(self):
        return self._location_longitude

    @property
    def location(self):
        return self._location_longitude, self._location_latitude
    
    @location.setter
    def location(self, value):
        self._location_latitude = value[0]
        self._location_longitude = value[1]
        self._location_last_set_time = value[2]
    
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
    def ev_charge_limits(self) -> EvChargeLimits:
        return self._ev_charge_limits

    @ev_charge_limits.setter
    def ev_charge_limits(self, value: EvChargeLimits):
        self._ev_charge_limits = value

    @property
    def fuel_driving_distance(self):
        return self._fuel_driving_distance

    @fuel_driving_distance.setter
    def fuel_driving_distance(self, value):
        self._fuel_driving_distance_value = value[0]
        self._fuel_driving_distance_unit = value[1]
        self._fuel_driving_distance = value[0]
