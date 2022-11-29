import datetime as dt
import logging
from dataclasses import dataclass

import requests

from .const import *
from .Token import Token
from .Vehicle import Vehicle

from .utils import (
    get_child_value,
    get_hex_temp_into_index,
    get_index_into_hex_temp,
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

class ApiImpl:
    data_timezone = dt.timezone.utc
    temperature_range = None

    def __init__(self) -> None:
        """Initialize."""

    def login(self, username: str, password: str) -> Token:
        """Login into cloud endpoints and return Token"""
        pass

    def get_vehicles(self, token: Token) -> list[Vehicle]:
        """Return all Vehicle instances for a given Token"""
        pass

    def refresh_vehicles(self, token: Token, vehicles: list[Vehicle]) -> None:
        """Refresh the vehicle data provided in get_vehicles. Required for Kia USA as key is session specific"""
        pass

    def get_last_updated_at(self, value) -> dt.datetime:
        """Convert last updated value of vehicle into into datetime"""
        pass

    def update_vehicle_with_cached_state(self, token: Token, vehicle: Vehicle) -> None:
        """Get cached vehicle data and update Vehicle instance with it"""
        pass

    def check_last_action_status(self, token: Token, vehicle: Vehicle, action_id: str) -> bool:
        """Check if a previous placed call was successful. Returns true if complete. False if not.  Does not confirm if successful only confirms if complete"""
        pass

    def force_refresh_vehicle_state(self, token: Token, vehicle: Vehicle) -> None:
        """Triggers the system to contact the car and get fresh data"""
        pass

    def update_geocoded_location(self, token: Token, vehicle: Vehicle, use_email: bool) -> None:

        email_parameter = ""
        if use_email == True:
            email_parameter = "&email=" + token.username

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
        
    def lock_action(self, token: Token, vehicle: Vehicle, action: str) -> str:
        """Lock or unlocks a vehicle.  Returns the tracking ID"""
        pass

    def start_climate(
        self,
        token: Token,
        vehicle: Vehicle,
        options: ClimateRequestOptions
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

    def set_charge_limits(self, token: Token, vehicle: Vehicle, ac: int, dc: int)-> str:
        """Sets charge limits. Returns the tracking ID"""
        pass

