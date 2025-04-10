"""ApiImpl.py"""

# pylint:disable=unnecessary-pass,missing-class-docstring,invalid-name,missing-function-docstring,wildcard-import,unused-wildcard-import,unused-argument,missing-timeout,logging-fstring-interpolation
import datetime as dt
import logging
from dataclasses import dataclass

import requests
from geopy.geocoders import GoogleV3
from requests.exceptions import JSONDecodeError

from .utils import get_child_value
from .Token import Token
from .Vehicle import Vehicle
from .const import (
    WINDOW_STATE,
    CHARGE_PORT_ACTION,
    ORDER_STATUS,
    DOMAIN,
    VALET_MODE_ACTION,
    VEHICLE_LOCK_ACTION,
    GEO_LOCATION_PROVIDERS,
    OPENSTREETMAP,
    GOOGLE,
)

_LOGGER = logging.getLogger(__name__)


@dataclass
class ClimateRequestOptions:
    set_temp: float = None
    duration: int = None
    defrost: bool = None
    climate: bool = None
    heating: int = None
    front_left_seat: int = None
    front_right_seat: int = None
    rear_left_seat: int = None
    rear_right_seat: int = None
    steering_wheel: int = None


@dataclass
class WindowRequestOptions:
    back_left: WINDOW_STATE = None
    back_right: WINDOW_STATE = None
    front_left: WINDOW_STATE = None
    front_right: WINDOW_STATE = None


@dataclass
class ScheduleChargingClimateRequestOptions:
    @dataclass
    class DepartureOptions:
        enabled: bool = None
        days: list[int] = None  # Sun=0, Mon=1, ..., Sat=6
        time: dt.time = None

    first_departure: DepartureOptions = None
    second_departure: DepartureOptions = None
    charging_enabled: bool = None
    off_peak_start_time: dt.time = None
    off_peak_end_time: dt.time = None
    off_peak_charge_only_enabled: bool = None
    climate_enabled: bool = None
    temperature: float = None
    temperature_unit: int = None
    defrost: bool = None


class ApiImpl:
    data_timezone = dt.timezone.utc
    temperature_range = None
    previous_latitude: float = None
    previous_longitude: float = None

    def __init__(self) -> None:
        """Initialize."""

    def login(self, username: str, password: str) -> Token:
        """Login into cloud endpoints and return Token"""
        pass

    def get_vehicles(self, token: Token) -> list[Vehicle]:
        """Return all Vehicle instances for a given Token"""
        pass

    def refresh_vehicles(self, token: Token, vehicles: list[Vehicle]) -> None:
        """Refresh the vehicle data provided in get_vehicles.
        Required for Kia USA as key is session specific"""
        return vehicles

    def update_vehicle_with_cached_state(self, token: Token, vehicle: Vehicle) -> None:
        """Get cached vehicle data and update Vehicle instance with it"""
        pass

    def test_token(self, token: Token) -> bool:
        """Test if token is valid
        Use any dummy request to test if token is still valid"""
        return True

    def check_action_status(
        self,
        token: Token,
        vehicle: Vehicle,
        action_id: str,
        synchronous: bool = False,
        timeout: int = 0,
    ) -> ORDER_STATUS:
        pass

    def force_refresh_vehicle_state(self, token: Token, vehicle: Vehicle) -> None:
        """Triggers the system to contact the car and get fresh data"""
        pass

    def update_geocoded_location(
        self,
        token: Token,
        vehicle: Vehicle,
        use_email: bool,
        provider: int = 1,
        API_KEY: str = None,
    ) -> None:
        if vehicle.location_latitude and vehicle.location_longitude:
            if (
                vehicle.geocode
                and vehicle.location_latitude == self.previous_latitude
                and vehicle.location_longitude == self.previous_longitude
            ):  # previous coordinates are the same, so keep last valid vehicle.geocode
                _LOGGER.debug(f"{DOMAIN} - Keeping last geocode location")
            elif GEO_LOCATION_PROVIDERS[provider] == OPENSTREETMAP:
                email_parameter = ""
                if use_email is True:
                    email_parameter = "&email=" + token.username

                url = (
                    "https://nominatim.openstreetmap.org/reverse?lat="
                    + str(vehicle.location_latitude)
                    + "&lon="
                    + str(vehicle.location_longitude)
                    + "&format=json&addressdetails=1&zoom=18"
                    + email_parameter
                )
                headers = {"user-agent": "curl/7.81.0"}
                response = requests.get(url, headers=headers)
                try:
                    response = response.json()
                except JSONDecodeError:
                    _LOGGER.warning(f"{DOMAIN} - failed geocode openstreetmap")
                    vehicle.geocode = None
                else:
                    vehicle.geocode = (
                        get_child_value(response, "display_name"),
                        get_child_value(response, "address"),
                    )
                    self.previous_latitude = vehicle.location_latitude
                    self.previous_longitude = vehicle.location_longitude
                    _LOGGER.debug(f"{DOMAIN} - geocode openstreetmap")
            elif GEO_LOCATION_PROVIDERS[provider] == GOOGLE:
                if API_KEY:
                    latlong = (vehicle.location_latitude, vehicle.location_longitude)
                    try:
                        geolocator = GoogleV3(api_key=API_KEY)
                        locations = geolocator.reverse(latlong)
                        if locations:
                            vehicle.geocode = locations
                            self.previous_latitude = vehicle.location_latitude
                            self.previous_longitude = vehicle.location_longitude
                            _LOGGER.debug(f"{DOMAIN} - geocode google")
                    except Exception as ex:  # pylint: disable=broad-except
                        _LOGGER.warning(f"{DOMAIN} - failed geocode Google: {ex}")
                        vehicle.geocode = None

    def lock_action(
        self, token: Token, vehicle: Vehicle, action: VEHICLE_LOCK_ACTION
    ) -> str:
        """Lock or unlocks a vehicle.  Returns the tracking ID"""
        pass

    def start_climate(
        self, token: Token, vehicle: Vehicle, options: ClimateRequestOptions
    ) -> str:
        """Starts climate or remote start.  Returns the tracking ID"""
        pass

    def stop_climate(self, token: Token, vehicle: Vehicle) -> str:
        """Stops climate or remote start.  Returns the tracking ID"""
        pass

    def start_charge(self, token: Token, vehicle: Vehicle) -> str:
        """Starts charge. Returns the tracking ID"""
        pass

    def stop_charge(self, token: Token, vehicle: Vehicle) -> str:
        """Stops charge. Returns the tracking ID"""
        pass

    def set_charge_limits(
        self, token: Token, vehicle: Vehicle, ac: int, dc: int
    ) -> str:
        """Sets charge limits. Returns the tracking ID"""
        pass

    def set_charging_current(self, token: Token, vehicle: Vehicle, level: int) -> str:
        """
        feature only available for some regions.
        Sets charge current level (1=100%, 2=90%, 3=60%). Returns the tracking ID
        """
        pass

    def set_windows_state(
        self, token: Token, vehicle: Vehicle, options: WindowRequestOptions
    ) -> str:
        """Opens or closes a particular window. Returns the tracking ID"""
        pass

    def charge_port_action(
        self, token: Token, vehicle: Vehicle, action: CHARGE_PORT_ACTION
    ) -> str:
        """Opens or closes the charging port of the car. Returns the tracking ID"""
        pass

    def update_month_trip_info(
        self, token: Token, vehicle: Vehicle, yyyymm_string: str
    ) -> None:
        """
        feature only available for some regions.
        Updates the vehicle.month_trip_info for the specified month.

        Default this information is None:

        month_trip_info: MonthTripInfo = None
        """
        pass

    def update_day_trip_info(
        self, token: Token, vehicle: Vehicle, yyyymmdd_string: str
    ) -> None:
        """
        feature only available for some regions.
        Updates the vehicle.day_trip_info information for the specified day.

        Default this information is None:

        day_trip_info: DayTripInfo = None
        """
        pass

    def schedule_charging_and_climate(
        self,
        token: Token,
        vehicle: Vehicle,
        options: ScheduleChargingClimateRequestOptions,
    ) -> str:
        """
        feature only available for some regions.
        Schedule charging and climate control. Returns the tracking ID
        """
        pass

    def start_hazard_lights(self, token: Token, vehicle: Vehicle) -> str:
        """Turns on the hazard lights for 30 seconds"""
        pass

    def start_hazard_lights_and_horn(self, token: Token, vehicle: Vehicle) -> str:
        """Turns on the hazard lights and horn for 30 seconds"""
        pass

    def valet_mode_action(
        self, token: Token, vehicle: Vehicle, action: VALET_MODE_ACTION
    ) -> str:
        """
        feature only available for some regions.
        Activate or Deactivate valet mode. Returns the tracking ID
        """
        pass

    def set_vehicle_to_load_discharge_limit(
        self, token: Token, vehicle: Vehicle, limit: int
    ) -> str:
        """
        feature only available for some regions.
        Set the vehicle to load limit. Returns the tracking ID
        """
        pass
