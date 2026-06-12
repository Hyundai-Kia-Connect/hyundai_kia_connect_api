# pylint:disable=missing-class-docstring,missing-function-docstring,wildcard-import,unused-wildcard-import,invalid-name,logging-fstring-interpolation
"""Vehicle class"""

import logging
import datetime
import typing
from dataclasses import dataclass, field

from .utils import get_float, get_safe_local_datetime
from .const import DISTANCE_UNITS

_LOGGER = logging.getLogger(__name__)


@dataclass
class TripInfo:
    """Trip Info"""

    hhmmss: str = None  # will not be filled by summary
    drive_time: int = None  # minutes
    idle_time: int = None  # minutes
    distance: float = None
    avg_speed: float = None
    max_speed: int = None


@dataclass
class DayTripCounts:
    """Day trip counts"""

    yyyymmdd: str = None
    trip_count: int = None


@dataclass
class MonthTripInfo:
    """Month Trip Info"""

    yyyymm: str = None
    summary: TripInfo = None
    day_list: list[DayTripCounts] = field(default_factory=list)


@dataclass
class DayTripInfo:
    """Day Trip Info"""

    yyyymmdd: str = None
    summary: TripInfo = None
    trip_list: list[TripInfo] = field(default_factory=list)


@dataclass
class DailyDrivingStats:
    # energy stats are expressed in watthours (Wh)
    date: datetime.datetime = None
    total_consumed: int = None
    engine_consumption: int = None
    climate_consumption: int = None
    onboard_electronics_consumption: int = None
    battery_care_consumption: int = None
    regenerated_energy: int = None
    distance: float = None
    distance_unit: str = DISTANCE_UNITS[1]  # set to kms by default


@dataclass
class VehicleProfile:
    # basic
    brand: str | None = None
    country: str | None = None
    ota_update_supported: bool | None = None
    remote_ota_update_supported: bool | None = None

    # device
    sim_status: str | None = None
    sim_start_date: str | None = None
    sim_end_date: str | None = None
    head_unit_type: str | None = None
    head_unit_model_name: str | None = None
    head_unit_version: str | None = None
    platform: str | None = None
    navi_applied: bool | None = None
    web_manual_url: str | None = None

    # option (raw API values)
    air_control_type: str | None = None
    driver_seat_location: str | None = None
    remote_control: str | None = None
    heating1: str | None = None
    heating_front_window: str | None = None
    steering_wheel_heat_option: str | None = None
    heating_steering_wheel: str | None = None
    heating_side_mirror: str | None = None
    heating_rear_window: str | None = None
    light_only_available: str | None = None
    horn_light_available: str | None = None
    hvac_temp_type: str | None = None
    remote_control_waiting_time: int | None = None
    window_safety_option2: int | None = None
    sunroof_option: str | None = None
    digital_key2: str | None = None
    remote_heat_control: str | None = None
    air_purifier_option: str | None = None
    dvrs_option: str | None = None
    ignition_control_option: str | None = None
    seat_heater_vent_front_left: int | None = None
    seat_heater_vent_front_right: int | None = None
    seat_heater_vent_rear_left: int | None = None
    seat_heater_vent_rear_right: int | None = None
    ev_alarm_option_info: str | None = None
    remote_air_ctrl_control_option: str | None = None

    # serviceOption
    battery_warning_service: bool | None = None
    schedule_link_service: bool | None = None
    center_user_profile_option: int | None = None
    final_destination_noti: bool | None = None
    valet_service_option: bool | None = None
    notification_support: bool | None = None
    remote_valet_act_option: bool | None = None
    alert_service_option: bool | None = None
    media_streaming_service: list[int] | None = None
    media_streaming_selection_option: bool | None = None
    idle_alert_setting_service: bool | None = None
    engine_idle_time_notification: bool | None = None
    send2car_option_info: int | None = None
    speed_event_support: bool | None = None

    # batteryType
    main_battery_type: int | None = None
    aux_battery_type: int | None = None

    # detailInfo
    sale_model_code: str | None = None
    body_type: str | None = None
    interior_color: str | None = None
    exterior_color: str | None = None

    # dtcCategory
    dtc_categories: list | None = None


@dataclass
class UserAccount:
    user_id: str | None = None
    email: str | None = None
    name: str | None = None
    mobile_number: str | None = None
    language: str | None = None
    country: str | None = None
    status: int | None = None
    sign_up_date: str | None = None
    pin_date: str | None = None


@dataclass
class Vehicle:
    id: str = None
    name: str = None
    model: str = None
    registration_date: str = None
    year: int = None
    VIN: str = None
    key: str = None
    # EU or Type 1 version:
    ccu_ccs2_protocol_support: int = None
    # Hyundai USA:
    generation: int = None
    # Not part of the API, enabled in our library for scanning.
    enabled: bool = True

    # Shared (EV/PHEV/HEV/IC)
    # General
    _total_driving_range: float = None
    _total_driving_range_value: float = None
    _total_driving_range_unit: str = None

    _odometer: float = None
    _odometer_value: float = None
    _odometer_unit: str = None

    _geocode_address: str = None
    _geocode_name: str = None

    car_battery_percentage: int = None
    engine_is_running: bool = None

    _last_updated_at: datetime.datetime = None
    timezone: datetime.timezone = datetime.timezone.utc  # default UTC

    dtc_count: typing.Union[int, None] = None
    dtc_descriptions: typing.Union[dict, None] = None

    smart_key_battery_warning_is_on: bool = None
    washer_fluid_warning_is_on: bool = None
    brake_fluid_warning_is_on: bool = None

    _outside_temperature: float = None
    _outside_temperature_value: float = None
    _outside_temperature_unit: str = None

    # Climate
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

    # Door Status
    is_locked: bool = None
    front_left_door_is_locked: bool = None
    front_right_door_is_locked: bool = None
    back_left_door_is_locked: bool = None
    back_right_door_is_locked: bool = None
    front_left_door_is_open: bool = None
    front_right_door_is_open: bool = None
    back_left_door_is_open: bool = None
    back_right_door_is_open: bool = None
    trunk_is_open: bool = None
    hood_is_open: bool = None

    # Window Status
    front_left_window_is_open: bool = None
    front_right_window_is_open: bool = None
    back_left_window_is_open: bool = None
    back_right_window_is_open: bool = None
    sunroof_is_open: bool = None
    supports_window_control: bool = None

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

    ev_charge_port_door_is_open: typing.Union[bool, None] = None
    ev_charging_power: typing.Union[float, None] = None  # Charging power in kW

    ev_charge_limits_dc: typing.Union[int, None] = None
    ev_charge_limits_ac: typing.Union[int, None] = None
    ev_charging_current: typing.Union[int, None] = (
        None  # Europe feature only, ac charging current limit
    )
    ev_v2l_discharge_limit: typing.Union[int, None] = None

    ev_v2l_status: typing.Union[bool, None] = None
    ev_v2x_status: typing.Union[bool, None] = None

    # energy consumed and regenerated since the vehicle was paired with the account
    # (so not necessarily for the vehicle's lifetime)
    # expressed in watt-hours (Wh)
    total_power_consumed: float = None  # Europe feature only
    total_power_regenerated: float = None  # Europe feature only
    # energy consumed in the last ~30 days
    # expressed in watt-hours (Wh)
    power_consumption_30d: float = None  # Europe feature only

    # feature only available for some regions (getter/setter for sorting)
    _daily_stats: list[DailyDrivingStats] = field(default_factory=list)

    # Other statuses from KiaCA logs
    accessory_on: bool = None
    ign3: bool = None
    remote_ignition: bool = None
    transmission_condition: str = None
    sleep_mode_check: bool = None

    # Lamp status fields (KiaUvoApiEU and CA)
    headlamp_status: str = None
    headlamp_left_low: bool = None
    headlamp_right_low: bool = None
    headlamp_left_high: bool = None
    headlamp_right_high: bool = None
    headlamp_left_bifunc: bool = None
    headlamp_right_bifunc: bool = None
    stop_lamp_left: bool = None
    stop_lamp_right: bool = None
    turn_signal_left_front: bool = None
    turn_signal_right_front: bool = None
    turn_signal_left_rear: bool = None
    turn_signal_right_rear: bool = None

    @property
    def daily_stats(self):
        return self._daily_stats

    @daily_stats.setter
    def daily_stats(self, value):
        result = value
        if result is not None and len(result) > 0:  # sort on decreasing date
            _LOGGER.debug(f"before daily_stats: {result}")
            result.sort(reverse=True, key=lambda k: k.date)
            _LOGGER.debug(f"after  daily_stats: {result}")
        self._daily_stats = result

    # feature only available for some regions (getter/setter for sorting)
    _month_trip_info: MonthTripInfo = None

    @property
    def month_trip_info(self):
        return self._month_trip_info

    @month_trip_info.setter
    def month_trip_info(self, value):
        result = value
        if (
            result is not None
            and hasattr(result, "day_list")
            and len(result.day_list) > 0
        ):  # sort on increasing yyyymmdd
            _LOGGER.debug(f"before month_trip_info: {result}")
            result.day_list.sort(key=lambda k: k.yyyymmdd)
            _LOGGER.debug(f"after  month_trip_info: {result}")
        self._month_trip_info = result

    # feature only available for some regions (getter/setter for sorting)
    _day_trip_info: DayTripInfo = None

    @property
    def day_trip_info(self):
        return self._day_trip_info

    @day_trip_info.setter
    def day_trip_info(self, value):
        result = value
        if (
            result is not None
            and hasattr(result, "trip_list")
            and len(result.trip_list) > 0
        ):  # sort on descending hhmmss
            _LOGGER.debug(f"before day_trip_info: {result}")
            result.trip_list.sort(reverse=True, key=lambda k: k.hhmmss)
            _LOGGER.debug(f"after day_trip_info: {result}")
        self._day_trip_info = result

    ev_battery_percentage: int = None
    ev_battery_pack_voltage: int = None
    ev_battery_chiller_rpm: int = None
    ev_battery_heating_state: bool = None
    _ev_battery_water_temperature: int = None
    _ev_battery_water_temperature_value: int = None
    _ev_battery_water_temperature_unit: str = None

    _ev_battery_temperature_min: int = None
    _ev_battery_temperature_min_value: int = None
    _ev_battery_temperature_min_unit: str = None

    _ev_battery_temperature_max: int = None
    _ev_battery_temperature_max_value: int = None
    _ev_battery_temperature_max_unit: str = None
    ev_battery_winter_mode: bool = None
    ev_battery_soh_percentage: int = None
    ev_battery_remain: int = None
    ev_battery_capacity: int = None
    ev_battery_is_charging: bool = None
    ev_battery_is_plugged_in: bool = None

    _ev_driving_range: float = None
    _ev_driving_range_value: float = None
    _ev_driving_range_unit: str = None

    _ev_estimated_current_charge_duration: int = None
    _ev_estimated_current_charge_duration_value: int = None
    _ev_estimated_current_charge_duration_unit: str = None

    _ev_estimated_fast_charge_duration: int = None
    _ev_estimated_fast_charge_duration_value: int = None
    _ev_estimated_fast_charge_duration_unit: str = None

    _ev_estimated_portable_charge_duration: int = None
    _ev_estimated_portable_charge_duration_value: int = None
    _ev_estimated_portable_charge_duration_unit: str = None

    ev_battery_precondition_enabled: bool = None

    _ev_estimated_station_charge_duration: int = None
    _ev_estimated_station_charge_duration_value: int = None
    _ev_estimated_station_charge_duration_unit: str = None

    _ev_target_range_charge_AC: typing.Union[float, None] = None
    _ev_target_range_charge_AC_value: typing.Union[float, None] = None
    _ev_target_range_charge_AC_unit: typing.Union[str, None] = None

    _ev_target_range_charge_DC: typing.Union[float, None] = None
    _ev_target_range_charge_DC_value: typing.Union[float, None] = None
    _ev_target_range_charge_DC_unit: typing.Union[str, None] = None

    ev_power_consumption_battery_cooling: typing.Union[float, None] = None
    ev_power_consumption_battery_heater: typing.Union[float, None] = None
    ev_power_consumption_air_conditioning: typing.Union[float, None] = None

    ev_first_departure_enabled: typing.Union[bool, None] = None
    ev_second_departure_enabled: typing.Union[bool, None] = None

    ev_first_departure_days: typing.Union[list, None] = None
    ev_second_departure_days: typing.Union[list, None] = None

    ev_first_departure_time: typing.Union[datetime.time, None] = None
    ev_second_departure_time: typing.Union[datetime.time, None] = None

    ev_first_departure_climate_enabled: typing.Union[bool, None] = None
    ev_second_departure_climate_enabled: typing.Union[bool, None] = None

    _ev_first_departure_climate_temperature: typing.Union[float, None] = None
    _ev_first_departure_climate_temperature_value: typing.Union[float, None] = None
    _ev_first_departure_climate_temperature_unit: typing.Union[str, None] = None

    _ev_second_departure_climate_temperature: typing.Union[float, None] = None
    _ev_second_departure_climate_temperature_value: typing.Union[float, None] = None
    _ev_second_departure_climate_temperature_unit: typing.Union[str, None] = None

    ev_first_departure_climate_defrost: typing.Union[bool, None] = None
    ev_second_departure_climate_defrost: typing.Union[bool, None] = None

    ev_off_peak_start_time: typing.Union[datetime.time, None] = None
    ev_off_peak_end_time: typing.Union[datetime.time, None] = None
    ev_off_peak_charge_only_enabled: typing.Union[bool, None] = None

    ev_schedule_charge_enabled: typing.Union[bool, None] = None

    # IC fields (PHEV/HEV/IC)
    _fuel_driving_range: float = None
    _fuel_driving_range_value: float = None
    _fuel_driving_range_unit: str = None
    fuel_level: float = None

    fuel_level_is_low: bool = None

    # Calculated fields
    engine_type: str = None

    # Debug fields
    data: dict = None
    profile: VehicleProfile | None = None

    @property
    def geocode(self):
        return self._geocode_name, self._geocode_address

    @geocode.setter
    def geocode(self, value):
        if value:
            self._geocode_name = value[0]
            self._geocode_address = value[1]
        else:
            self._geocode_name = None
            self._geocode_address = None

    @property
    def total_driving_range(self):
        return self._total_driving_range

    @property
    def total_driving_range_unit(self):
        return self._total_driving_range_unit

    @total_driving_range.setter
    def total_driving_range(self, value):
        self._total_driving_range_value = value[0]
        self._total_driving_range_unit = value[1]
        self._total_driving_range = value[0]

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
    def last_updated_at(self):
        return self._last_updated_at

    @last_updated_at.setter
    def last_updated_at(self, value):
        # workaround for: Timestamp of "last_updated_at" sensor is wrong #931
        # https://github.com/Hyundai-Kia-Connect/kia_uvo/issues/931#issuecomment-2381569934
        newest_updated_at = get_safe_local_datetime(value)
        previous_updated_at = self._last_updated_at
        if newest_updated_at and previous_updated_at:  # both filled
            if newest_updated_at < previous_updated_at:
                utcoffset = newest_updated_at.utcoffset()
                newest_updated_at_corrected = newest_updated_at + utcoffset
                if newest_updated_at_corrected >= previous_updated_at:
                    newest_updated_at = newest_updated_at_corrected
                if newest_updated_at < previous_updated_at:
                    newest_updated_at = previous_updated_at  # keep old because newer
        self._last_updated_at = newest_updated_at

    @property
    def location_latitude(self):
        return self._location_latitude

    @property
    def location_longitude(self):
        return self._location_longitude

    @property
    def location(self):
        return self._location_longitude, self._location_latitude

    @property
    def location_last_updated_at(self):
        """
        return last location datetime.
        last_updated_at and location_last_updated_at can be different.
        The newest of those 2 can be computed by the caller.
        """
        return self._location_last_set_time

    @location.setter
    def location(self, value):
        self._location_latitude = value[0]
        self._location_longitude = value[1]
        self._location_last_set_time = get_safe_local_datetime(value[2])

    @property
    def odometer(self):
        return self._odometer

    @property
    def odometer_unit(self):
        return self._odometer_unit

    @odometer.setter
    def odometer(self, value):
        float_value = get_float(value[0])
        self._odometer_value = float_value
        self._odometer_unit = value[1]
        self._odometer = float_value

    @property
    def outside_temperature(self):
        return self._outside_temperature

    @outside_temperature.setter
    def outside_temperature(self, value):
        self._outside_temperature_value = value[0]
        self._outside_temperature_unit = value[1]
        self._outside_temperature = value[0]

    @property
    def air_temperature(self):
        return self._air_temperature

    @air_temperature.setter
    def air_temperature(self, value):
        self._air_temperature_value = value[0]
        self._air_temperature_unit = value[1]
        self._air_temperature = value[0] if value[0] != "OFF" else None

    @property
    def ev_battery_water_temperature(self):
        return self._ev_battery_water_temperature

    @property
    def ev_battery_water_temperature_unit(self):
        return self._ev_battery_water_temperature_unit

    @ev_battery_water_temperature.setter
    def ev_battery_water_temperature(self, value):
        self._ev_battery_water_temperature_value = value[0]
        self._ev_battery_water_temperature_unit = value[1]
        self._ev_battery_water_temperature = value[0]

    @property
    def ev_battery_temperature_min(self):
        return self._ev_battery_temperature_min

    @property
    def ev_battery_temperature_min_unit(self):
        return self._ev_battery_temperature_min_unit

    @ev_battery_temperature_min.setter
    def ev_battery_temperature_min(self, value):
        self._ev_battery_temperature_min_value = value[0]
        self._ev_battery_temperature_min_unit = value[1]
        self._ev_battery_temperature_min = value[0]

    @property
    def ev_battery_temperature_max(self):
        return self._ev_battery_temperature_max

    @property
    def ev_battery_temperature_max_unit(self):
        return self._ev_battery_temperature_max_unit

    @ev_battery_temperature_max.setter
    def ev_battery_temperature_max(self, value):
        self._ev_battery_temperature_max_value = value[0]
        self._ev_battery_temperature_max_unit = value[1]
        self._ev_battery_temperature_max = value[0]

    @property
    def ev_driving_range(self):
        return self._ev_driving_range

    @property
    def ev_driving_range_unit(self):
        return self._ev_driving_range_unit

    @ev_driving_range.setter
    def ev_driving_range(self, value):
        self._ev_driving_range_value = value[0]
        self._ev_driving_range_unit = value[1]
        self._ev_driving_range = value[0]

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
    def ev_target_range_charge_AC(self):
        return self._ev_target_range_charge_AC

    @property
    def ev_target_range_charge_AC_unit(self):
        return self._ev_target_range_charge_AC_unit

    @ev_target_range_charge_AC.setter
    def ev_target_range_charge_AC(self, value):
        self._ev_target_range_charge_AC_value = value[0]
        self._ev_target_range_charge_AC_unit = value[1]
        self._ev_target_range_charge_AC = value[0]

    @property
    def ev_target_range_charge_DC(self):
        return self._ev_target_range_charge_DC

    @property
    def ev_target_range_charge_DC_unit(self):
        return self._ev_target_range_charge_DC_unit

    @ev_target_range_charge_DC.setter
    def ev_target_range_charge_DC(self, value):
        self._ev_target_range_charge_DC_value = value[0]
        self._ev_target_range_charge_DC_unit = value[1]
        self._ev_target_range_charge_DC = value[0]

    @property
    def ev_first_departure_climate_temperature(self):
        return self._ev_first_departure_climate_temperature

    @property
    def ev_first_departure_climate_temperature_unit(self):
        return self._ev_first_departure_climate_temperature_unit

    @ev_first_departure_climate_temperature.setter
    def ev_first_departure_climate_temperature(self, value):
        self._ev_first_departure_climate_temperature_value = value[0]
        self._ev_first_departure_climate_temperature_unit = value[1]
        self._ev_first_departure_climate_temperature = value[0]

    @property
    def ev_second_departure_climate_temperature(self):
        return self._ev_second_departure_climate_temperature

    @property
    def ev_second_departure_climate_temperature_unit(self):
        return self._ev_second_departure_climate_temperature_unit

    @ev_second_departure_climate_temperature.setter
    def ev_second_departure_climate_temperature(self, value):
        self._ev_second_departure_climate_temperature_value = value[0]
        self._ev_second_departure_climate_temperature_unit = value[1]
        self._ev_second_departure_climate_temperature = value[0]

    @property
    def fuel_driving_range(self):
        return self._fuel_driving_range

    @fuel_driving_range.setter
    def fuel_driving_range(self, value):
        self._fuel_driving_range_value = value[0]
        self._fuel_driving_range_unit = value[1]
        self._fuel_driving_range = value[0]

    # Capability properties from VehicleProfile (flat, for HA getattr pattern)

    @property
    def steering_wheel_heater_supported(self) -> bool | None:
        if self.profile is None:
            return None
        return self.profile.heating_steering_wheel == "1"

    @property
    def side_mirror_heater_supported(self) -> bool | None:
        if self.profile is None:
            return None
        return self.profile.heating_side_mirror == "1"

    @property
    def rear_window_heater_supported(self) -> bool | None:
        if self.profile is None:
            return None
        return self.profile.heating_rear_window == "1"

    @property
    def sunroof_supported(self) -> bool | None:
        if self.profile is None:
            return None
        return self.profile.sunroof_option == "1"

    @property
    def digital_key_supported(self) -> bool | None:
        if self.profile is None:
            return None
        return self.profile.digital_key2 != "0"

    @property
    def air_purifier_supported(self) -> bool | None:
        if self.profile is None:
            return None
        return self.profile.air_purifier_option == "1"

    @property
    def remote_heat_control_supported(self) -> bool | None:
        if self.profile is None:
            return None
        return self.profile.remote_heat_control != "0"

    @property
    def ignition_control_supported(self) -> bool | None:
        if self.profile is None:
            return None
        return self.profile.ignition_control_option == "1"

    @property
    def horn_light_supported(self) -> bool | None:
        if self.profile is None:
            return None
        return self.profile.horn_light_available == "1"

    @property
    def light_only_supported(self) -> bool | None:
        if self.profile is None:
            return None
        return self.profile.light_only_available == "1"

    @property
    def ev_alarm_supported(self) -> bool | None:
        if self.profile is None:
            return None
        return self.profile.ev_alarm_option_info != "0"

    @property
    def front_window_heating_supported(self) -> bool | None:
        if self.profile is None:
            return None
        return self.profile.heating_front_window == "1"

    @property
    def is_left_hand_drive(self) -> bool | None:
        if self.profile is None:
            return None
        return self.profile.driver_seat_location == "L"
