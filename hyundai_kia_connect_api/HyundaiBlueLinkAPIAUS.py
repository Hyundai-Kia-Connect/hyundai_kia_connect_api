#Austrialia API

import json
import logging
import datetime as dt
import re

import requests
import pytz

from .ApiImpl import ApiImpl, ClimateRequestOptions
from .const import (
    BRAND_HYUNDAI,
    BRAND_KIA,
    BRANDS,
    DOMAIN,
    DISTANCE_UNITS,
    TEMPERATURE_UNITS,
    SEAT_STATUS,
    ENGINE_TYPES,
    VEHICLE_LOCK_ACTION,
    SEAT_STATUS,
    ENGINE_TYPES,
)
from .Token import Token
from .utils import (
    get_child_value,
    get_hex_temp_into_index,
    get_index_into_hex_temp,
)
from .Vehicle import EvChargeLimits, Vehicle

_LOGGER = logging.getLogger(__name__)


class HyundaiBlueLinkAPIAUS(ApiImpl):
    data_timezone = dt.timezone.utc
    temperature_range = [x * 0.5 for x in range(28, 64)]
   

    def __init__(self, region: int, brand: int) -> None:
      self.API_URL: str = "https://" + self.BASE_URL + "/tods/api/"
      self.API_HEADERS = {
          "content-type": "application/json;charset=UTF-8",
          "accept": "application/json, text/plain, */*",
          "accept-encoding": "gzip, deflate, br",
          "accept-language": "en-US,en;q=0.9",
          "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/75.0.3770.142 Safari/537.36",
          "host": self.BASE_URL,
          "origin": "https://" + self.BASE_URL,
          "referer": "https://" + self.BASE_URL + "/login",
          "from": "SPA",
          "language": "0",
          "offset": "0",
          "sec-fetch-dest": "empty",
          "sec-fetch-mode": "cors",
          "sec-fetch-site": "same-origin",
      }

    def login(self, username: str, password: str) -> Token:
        """Login into cloud endpoints and return Token"""
        url = self.API_URL + "lgn"
        data = {"loginId": username, "password": password}
        headers = self.API_HEADERS
        response = requests.post(url, json=data, headers=headers)
        _LOGGER.debug(f"{DOMAIN} - Sign In Response {response.text}")
        response = response.json()
        response = response["result"]
        access_token = response["accessToken"]
        refresh_token = response["refreshToken"]
        _LOGGER.debug(f"{DOMAIN} - Access Token Value {access_token}")
        _LOGGER.debug(f"{DOMAIN} - Refresh Token Value {refresh_token}")

        valid_until = dt.datetime.now(pytz.utc) + dt.timedelta(hours=23)

        return Token(
            username=username,
            password=password,
            access_token=access_token,
            refresh_token=refresh_token,
            valid_until=valid_until,
        )

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

    def check_action_status(self, token: Token, vehicle: Vehicle, action_id: str):
        """Check if a previous placed call was successful"""
        pass

    def force_refresh_vehicle_state(self, token: Token, vehicle: Vehicle) -> None:
        """Triggers the system to contact the car and get fresh data"""
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

    def get_charge_limits(self, token: Token, vehicle: Vehicle) -> EvChargeLimits:
        pass

    def set_charge_limits(
        self, token: Token, vehicle: Vehicle, limits: EvChargeLimits) -> str:
        """Sets charge limits. Returns the tracking ID"""
        pass
