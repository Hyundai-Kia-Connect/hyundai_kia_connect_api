import datetime as dt
import logging
from dataclasses import dataclass

import requests

from .const import *
from .Token import Token
from .Vehicle import Vehicle

_LOGGER = logging.getLogger(__name__)



@dataclass
class ClimateRequestOptions:
    set_temp: str = None
    duration: int = None
    defrost = None
    climate: int = None
    heating: int = None


class ApiImpl:
    data_timezone = dt.timezone.utc
    temperature_range = None

    def __init__(self) -> None:
        """Initialize."""
        self.last_action_tracked = False
        self.supports_soc_range = True

    def login(self, username: str, password: str) -> Token:
        """Login into cloud endpoints and return Token"""
        pass

    def get_vehicles(self, token: Token) -> list[Vehicle]:
        """Return all Vehicle instances for a given Token"""
        pass

    def get_last_updated_at(self, value) -> dt.datetime:
        """Convert last updated value of vehicle into into datetime"""
        pass

    def update_vehicle_with_cached_state(self, token: Token, vehicle: Vehicle) -> None:
        """Get cached vehicle data and update Vehicle instance with it"""
        pass

    def get_fresh_vehicle_state(self, token: Token, vehicle: Vehicle) -> None:
        pass

    def check_last_action_status(self, token: Token, vehicle: Vehicle):
        pass

    def get_geocoded_location(self, lat, lon) -> dict:
        email_parameter = ""
        if self.use_email_with_geocode_api == True:
            email_parameter = "&email=" + self.username

        url = (
            "https://nominatim.openstreetmap.org/reverse?lat="
            + str(lat)
            + "&lon="
            + str(lon)
            + "&format=json&addressdetails=1&zoom=18"
            + email_parameter
        )
        response = requests.get(url)
        response = response.json()
        return response

    def lock_action(self, token: Token, vehicle: Vehicle, action: str) -> None:
        pass

    def start_climate(
        self,
        token: Token,
        vehicle: Vehicle,
        options: ClimateRequestOptions
    ) -> None:
        pass

    def stop_climate(self, token: Token, vehicle: Vehicle) -> None:
        pass

    def start_charge(self, token: Token, vehicle: Vehicle) -> None:
        pass

    def stop_charge(self, token: Token, vehicle: Vehicle) -> None:
        pass

    def set_charge_limits(
        self, token: Token, vehicle: Vehicle, ac_limit: int, dc_limit: int
    ) -> None:
        pass
