import datetime as dt
import logging
import re
from dataclasses import dataclass

import requests

from .const import *
from .Token import Token
from .Vehicle import Vehicle, ClimateRequestOptions

from .utils import (
    get_child_value,
)

_LOGGER = logging.getLogger(__name__)


class ApiImpl:
    data_timezone = dt.timezone.utc
    temperature_range = None

    def __init__(self) -> None:
        """Initialize."""
        self.token: Token = None

    def login(self, username: str, password: str, pin: str):
        """Login into cloud endpoints and return Token"""
        pass

    def get_vehicles(self) -> list[Vehicle]:
        """Return all Vehicle instances for a given Token"""
        pass

    def refresh_vehicles(self, vehicles: list[Vehicle]) -> None:
        """Refresh the vehicle data provided in get_vehicles. Required for Kia USA as key is session specific"""
        pass

    def get_last_updated_at(self, value) -> dt.datetime:
        """Convert last updated value of vehicle into into datetime"""
        if value is not None:
            m = re.match(r"(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})", value)
            _LOGGER.debug(f"{DOMAIN} - last_updated_at - before {value}")
            converted_value = dt.datetime(
                year=int(m.group(1)),
                month=int(m.group(2)),
                day=int(m.group(3)),
                hour=int(m.group(4)),
                minute=int(m.group(5)),
                second=int(m.group(6)),
                tzinfo=self.data_timezone,
            )
            _LOGGER.debug(f"{DOMAIN} - last_updated_at - after {converted_value}")
            return converted_value

    def update_vehicle_with_cached_state(self, vehicle: Vehicle) -> None:
        """Get cached vehicle data and update Vehicle instance with it"""
        pass

    def check_last_action_status(self, vehicle: Vehicle, action_id: str) -> bool:
        """Check if a previous placed call was successful. Returns true if complete. False if not.  Does not confirm if successful only confirms if complete"""
        pass

    def force_refresh_vehicle_state(self, vehicle: Vehicle) -> None:
        """Triggers the system to contact the car and get fresh data"""
        pass

    def update_geocoded_location(self, vehicle: Vehicle, use_email: bool) -> None:

        email_parameter = ""
        if use_email == True:
            email_parameter = "&email=" + self.token.username

        url = (
            "https://nominatim.openstreetmap.org/reverse?lat="
            + str(vehicle.location_latitude)
            + "&lon="
            + str(vehicle.location_longitude)
            + "&format=json&addressdetails=1&zoom=18"
            + email_parameter
        )
        response = requests.get(url)
        response = response.json()
        vehicle.geocode = (get_child_value(response, "display_name"), get_child_value(response, "address"))

    def lock_action(self, vehicle: Vehicle, action: str) -> str:
        """Lock or unlocks a vehicle.  Returns the tracking ID"""
        pass

    def start_climate(
        self,
        vehicle: Vehicle,
        options: ClimateRequestOptions
    ) -> str:
        """Starts climate or remote start.  Returns the tracking ID"""

        pass

    def stop_climate(self, vehicle: Vehicle) -> str:
        """Stops climate or remote start.  Returns the tracking ID"""
        pass

    def start_charge(self, vehicle: Vehicle) -> str:
        """Starts charge. Returns the tracking ID"""
        pass

    def stop_charge(self, vehicle: Vehicle) -> str:
        """Stops charge. Returns the tracking ID"""
        pass

    def set_charge_limits(self, token: Token, vehicle: Vehicle, ac: int, dc: int)-> str:
        """Sets charge limits. Returns the tracking ID"""
        pass
