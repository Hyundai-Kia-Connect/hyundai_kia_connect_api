"""HyundaiBlueLinkApiBR.py"""

# pylint:disable=logging-fstring-interpolation,invalid-name,broad-exception-caught,unused-argument,missing-function-docstring,line-too-long

import logging
import datetime as dt
from datetime import timedelta
from urllib.parse import urljoin, urlparse
import requests

from .ApiImpl import ApiImpl
from .Token import Token
from .Vehicle import Vehicle, MonthTripInfo, DayTripInfo, DayTripCounts, TripInfo
from .utils import parse_date_br
from .const import (
    BRAND_HYUNDAI,
    BRANDS,
    DISTANCE_UNITS,
    DOMAIN,
    SEAT_STATUS,
    ENGINE_TYPES,
)
from .exceptions import APIError

_LOGGER = logging.getLogger(__name__)


class HyundaiBlueLinkApiBR(ApiImpl):
    """Brazilian Hyundai BlueLink API implementation."""

    data_timezone = dt.timezone(dt.timedelta(hours=-3))  # Brazil (BRT/BRST)

    def __init__(self, region: int, brand: int, language: str = "pt-BR"):
        if BRANDS[brand] != BRAND_HYUNDAI:
            raise APIError(
                f"Unknown brand {BRANDS[brand]} for region Brazil. "
                "Only Hyundai is supported."
            )

        self.language = language
        self.base_url = "br-ccapi.hyundai.com.br"
        self.api_url = f"https://{self.base_url}/api/v1/"
        self.ccsp_device_id = "c6e5815b-3057-4e5e-95d5-e3d5d1d2093e"
        self.ccsp_service_id = "03f7df9b-7626-4853-b7bd-ad1e8d722bd5"
        self.ccsp_application_id = "513a491a-0d7c-4d6a-ac03-a2df127d73b0"
        self.basic_authorization_header = (
            "Basic MDNmN2RmOWItNzYyNi00ODUzLWI3YmQtYWQxZThkNzIyYmQ1On"
            "lRejJiYzZDbjhPb3ZWT1I3UkRXd3hUcVZ3V0czeUtCWUZEZzBIc09Yc3l4eVBsSA=="
        )

        self.api_headers = {
            "Content-Type": "application/json; charset=UTF-8",
            "Accept": "application/json, text/plain, */*",
            "Accept-Encoding": "br;q=1.0, gzip;q=0.9, deflate;q=0.8",
            "Accept-Language": "pt-BR;q=1.0, en-US;q=0.9",
            "User-Agent": "BR_BlueLink/1.0.14 (com.hyundai.bluelink.br; build:10132; iOS 18.4.0) Alamofire/5.9.1",
            "Host": self.base_url,
            "offset": "-3",
            "ccuCCS2ProtocolSupport": "0",
        }

        self.session = requests.Session()
        self.temperature_range = range(62, 82)

    def _build_api_url(self, path: str) -> str:
        """Build full API URL from path."""
        return urljoin(self.api_url, path.lstrip("/"))

    def _get_authenticated_headers(self, token: Token) -> dict:
        """Get headers with authentication."""
        headers = dict(self.api_headers)
        headers["ccsp-device-id"] = self.ccsp_device_id
        headers["ccsp-application-id"] = self.ccsp_application_id
        headers["Authorization"] = f"Bearer {token.access_token}"
        return headers

    def _get_cookies(self) -> dict:
        """Request cookies from the API for authentication."""
        params = {
            "response_type": "code",
            "client_id": self.ccsp_service_id,
            "redirect_uri": self._build_api_url("/user/oauth2/redirect"),
        }

        url = self._build_api_url("/user/oauth2/authorize")
        _LOGGER.debug(f"{DOMAIN} - Requesting cookies from {url}")
        response = self.session.get(
            url, params=params
        )  # Use self.session instead of requests
        response.raise_for_status()
        cookies = response.cookies.get_dict()
        _LOGGER.debug(f"{DOMAIN} - Got cookies: {cookies}")
        return cookies

    def _get_authorization_code(
        self, cookies: dict, username: str, password: str
    ) -> str:
        """Get authorization code from redirect URL."""
        url = self._build_api_url("/user/signin")
        data = {"email": username, "password": password}

        headers = {
            "Referer": "https://br-ccapi.hyundai.com.br/web/v1/user/signin",
            "Accept-Encoding": "gzip, deflate, br",
            "Accept": "*/*",
            "Connection": "keep-alive",
            "Content-Type": "text/plain;charset=UTF-8",
            "Host": self.api_headers["Host"],
            "Accept-Language": "pt-BR,en-US;q=0.9,en;q=0.8",
            "Origin": "https://br-ccapi.hyundai.com.br",
            "User-Agent": (
                "Mozilla/5.0 (iPhone; CPU iPhone OS 18_4 like Mac OS X) "
                "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148_CCS_APP_iOS"
            ),
        }

        response = self.session.post(url, json=data, cookies=cookies, headers=headers)
        response.raise_for_status()
        response_data = response.json()

        _LOGGER.debug(f"{DOMAIN} - Got redirect URL")
        parsed_url = urlparse(response_data["redirectUrl"])
        authorization_code = parsed_url.query.split("=")[1]
        return authorization_code

    def _get_auth_response(self, authorization_code: str) -> dict:
        """Request access token from the API."""
        url = self._build_api_url("/user/oauth2/token")
        body = {
            "client_id": self.ccsp_service_id,
            "grant_type": "authorization_code",
            "code": authorization_code,
            "redirect_uri": self._build_api_url("/user/oauth2/redirect"),
        }
        headers = {
            "Content-Type": "application/x-www-form-urlencoded; charset=utf-8",
            "User-Agent": self.api_headers["User-Agent"],
            "Authorization": self.basic_authorization_header,
        }

        response = requests.post(url, data=body, headers=headers)
        response.raise_for_status()
        return response.json()

    def login(self, username: str, password: str) -> Token:
        """Login to Brazilian Hyundai API."""
        _LOGGER.debug(f"{DOMAIN} - Logging in to Brazilian API")

        cookies = self._get_cookies()
        authorization_code = self._get_authorization_code(cookies, username, password)
        auth_response = self._get_auth_response(authorization_code)

        expires_in_seconds = auth_response["expires_in"]
        expires_at = dt.datetime.now(dt.timezone.utc) + timedelta(
            seconds=expires_in_seconds
        )

        return Token(
            access_token=auth_response["access_token"],
            refresh_token=auth_response["refresh_token"],
            valid_until=expires_at,
            username=username,
            password=password,
        )

    def get_vehicles(self, token: Token) -> list:
        """Get list of vehicles."""
        url = self._build_api_url("/spa/vehicles")
        headers = self._get_authenticated_headers(token)

        response = self.session.get(url, headers=headers)
        response.raise_for_status()
        response_data = response.json()

        _LOGGER.debug(f"{DOMAIN} - Got vehicles response")

        result = []
        for entry in response_data["resMsg"]["vehicles"]:
            # Map vehicle type to engine type
            vehicle_type = entry["type"]
            if vehicle_type == "GN":
                entry_engine_type = ENGINE_TYPES.ICE
            elif vehicle_type == "EV":
                entry_engine_type = ENGINE_TYPES.EV
            elif vehicle_type in ["PHEV", "PE"]:
                entry_engine_type = ENGINE_TYPES.PHEV
            elif vehicle_type == "HV":
                entry_engine_type = ENGINE_TYPES.HEV
            else:
                entry_engine_type = ENGINE_TYPES.ICE

            vehicle = Vehicle(
                id=entry["vehicleId"],
                name=entry["nickname"],
                model=entry["vehicleName"],
                registration_date=entry["regDate"],
                VIN=entry["vin"],
                timezone=self.data_timezone,
                engine_type=entry_engine_type,
                ccu_ccs2_protocol_support=entry.get("ccuCCS2ProtocolSupport", 0),
            )
            result.append(vehicle)

        return result

    def _get_vehicle_state(
        self, token: Token, vehicle: Vehicle, force_refresh: bool = False
    ) -> dict:
        """Get vehicle state (cached or forced refresh)."""
        url = self._build_api_url(f"/spa/vehicles/{vehicle.id}")

        if not vehicle.ccu_ccs2_protocol_support:
            url = url + "/status/latest"
        else:
            url = url + "/ccs2/carstatus/latest"

        headers = self._get_authenticated_headers(token)
        if force_refresh:
            headers["REFRESH"] = "true"

        _LOGGER.debug(f"{DOMAIN} - Getting vehicle state (force={force_refresh})")
        response = self.session.get(url, headers=headers)
        response.raise_for_status()
        return response.json()["resMsg"]

    def _get_vehicle_location(self, token: Token, vehicle: Vehicle) -> dict:
        """Get vehicle location."""
        url = self._build_api_url(f"/spa/vehicles/{vehicle.id}/location/park")
        headers = self._get_authenticated_headers(token)

        try:
            response = self.session.get(url, headers=headers)
            response.raise_for_status()
            location_data = response.json()["resMsg"]
            _LOGGER.debug(f"{DOMAIN} - Got vehicle location")
            return location_data
        except Exception as e:
            _LOGGER.warning(f"{DOMAIN} - Failed to get vehicle location: {e}")
            return None

    def _update_vehicle_properties(self, vehicle: Vehicle, state: dict) -> None:
        """Update vehicle properties from state."""
        # Parse timestamp
        if state.get("time"):
            vehicle.last_updated_at = parse_date_br(state["time"], self.data_timezone)
        else:
            vehicle.last_updated_at = dt.datetime.now(self.data_timezone)

        # Basic vehicle status
        vehicle.engine_is_running = state.get("engine", False)
        vehicle.air_control_is_on = state.get("airCtrlOn", False)

        # Battery (12V car battery, not EV battery)
        if battery := state.get("battery"):
            vehicle.car_battery_percentage = battery.get("batSoc")

        # Temperature
        if air_temp := state.get("airTemp"):
            temp_value = air_temp.get("value")
            temp_unit = air_temp.get("unit")
            # Handle special values: "00H" means off, or hex temperature values
            # For now, only set if it's a valid numeric value
            if temp_value and temp_value != "00H":
                try:
                    # Try to parse as hex if it contains 'H'
                    if "H" in str(temp_value):
                        # Will be handled in future if needed
                        pass
                    else:
                        vehicle.air_temperature = (temp_value, temp_unit)
                except (ValueError, TypeError, KeyError):
                    pass

        # Fuel information
        vehicle.fuel_level = state.get("fuelLevel")
        vehicle.fuel_level_is_low = state.get("lowFuelLight", False)

        # Driving range (DTE = Distance To Empty)
        if dte := state.get("dte"):
            vehicle.fuel_driving_range = (
                dte.get("value"),
                DISTANCE_UNITS.get(dte.get("unit")),
            )

        # Doors
        door_state = state.get("doorOpen", {})
        vehicle.is_locked = state.get("doorLock", True)
        vehicle.front_left_door_is_open = bool(door_state.get("frontLeft"))
        vehicle.front_right_door_is_open = bool(door_state.get("frontRight"))
        vehicle.back_left_door_is_open = bool(door_state.get("backLeft"))
        vehicle.back_right_door_is_open = bool(door_state.get("backRight"))
        vehicle.hood_is_open = state.get("hoodOpen", False)
        vehicle.trunk_is_open = state.get("trunkOpen", False)

        # Windows
        window_state = state.get("windowOpen", {})
        vehicle.front_left_window_is_open = bool(window_state.get("frontLeft"))
        vehicle.front_right_window_is_open = bool(window_state.get("frontRight"))
        vehicle.back_left_window_is_open = bool(window_state.get("backLeft"))
        vehicle.back_right_window_is_open = bool(window_state.get("backRight"))

        # Climate control
        vehicle.defrost_is_on = state.get("defrost", False)

        # Steering wheel heat: 0=off, 1=on, 2=unknown/not available
        steer_heat = state.get("steerWheelHeat", 0)
        vehicle.steering_wheel_heater_is_on = steer_heat == 1

        # Side/back window heat: 0=off, 1=on, 2=unknown
        side_heat = state.get("sideBackWindowHeat", 0)
        vehicle.back_window_heater_is_on = side_heat == 1

        # Seat heater/ventilation status
        # Values: 0=off, 1=level1, 2=level2, 3=level3, etc.
        seat_state = state.get("seatHeaterVentState", {})
        vehicle.front_left_seat_status = SEAT_STATUS.get(
            seat_state.get("drvSeatHeatState")
        )
        vehicle.front_right_seat_status = SEAT_STATUS.get(
            seat_state.get("astSeatHeatState")
        )
        vehicle.rear_left_seat_status = SEAT_STATUS.get(
            seat_state.get("rlSeatHeatState")
        )
        vehicle.rear_right_seat_status = SEAT_STATUS.get(
            seat_state.get("rrSeatHeatState")
        )

        # Tire pressure warnings
        # Note: Brazilian Creta only has "all" indicator, not individual sensors
        tire_lamp = state.get("tirePressureLamp", {})
        vehicle.tire_pressure_all_warning_is_on = bool(
            tire_lamp.get("tirePressureLampAll")
        )

        # Set individual tire warnings to match "all" if they don't exist
        # (Some vehicles may have individual sensors)
        tire_all = bool(tire_lamp.get("tirePressureLampAll"))
        vehicle.tire_pressure_rear_left_warning_is_on = bool(
            tire_lamp.get("tirePressureWarningLampRearLeft", tire_all)
        )
        vehicle.tire_pressure_front_left_warning_is_on = bool(
            tire_lamp.get("tirePressureWarningLampFrontLeft", tire_all)
        )
        vehicle.tire_pressure_front_right_warning_is_on = bool(
            tire_lamp.get("tirePressureWarningLampFrontRight", tire_all)
        )
        vehicle.tire_pressure_rear_right_warning_is_on = bool(
            tire_lamp.get("tirePressureWarningLampRearRight", tire_all)
        )

        # Warnings and alerts
        vehicle.washer_fluid_warning_is_on = state.get("washerFluidStatus", False)
        vehicle.brake_fluid_warning_is_on = state.get("breakOilStatus", False)
        vehicle.smart_key_battery_warning_is_on = state.get(
            "smartKeyBatteryWarning", False
        )

        # Store raw data for future use
        vehicle.data = state

    def _update_vehicle_location(self, vehicle: Vehicle, location_data: dict) -> None:
        """Update vehicle location from location data."""
        if not location_data:
            return

        coord = location_data.get("coord", {})
        lat = coord.get("lat")
        lon = coord.get("lng") or coord.get("lon")
        time_str = location_data.get("time")

        if lat and lon:
            location_time = (
                parse_date_br(time_str, self.data_timezone) if time_str else None
            )
            vehicle.location = (lat, lon, location_time)

    def update_vehicle_with_cached_state(self, token: Token, vehicle: Vehicle) -> None:
        """Update vehicle with cached state from API."""
        state = self._get_vehicle_state(token, vehicle, force_refresh=False)
        location_data = self._get_vehicle_location(token, vehicle)

        self._update_vehicle_properties(vehicle, state)
        self._update_vehicle_location(vehicle, location_data)

    def force_refresh_vehicle_state(self, token: Token, vehicle: Vehicle) -> None:
        """Force refresh vehicle state (wakes up the vehicle)."""
        state = self._get_vehicle_state(token, vehicle, force_refresh=True)
        location_data = self._get_vehicle_location(token, vehicle)

        self._update_vehicle_properties(vehicle, state)
        self._update_vehicle_location(vehicle, location_data)

    def update_month_trip_info(
        self, token: Token, vehicle: Vehicle, yyyymm_string: str
    ) -> None:
        """Update monthly trip info."""
        url = self._build_api_url(f"/spa/vehicles/{vehicle.id}/tripinfo")
        data = {"tripPeriodType": 0, "setTripMonth": yyyymm_string}

        headers = self._get_authenticated_headers(token)
        response = self.session.post(url, json=data, headers=headers)

        try:
            response.raise_for_status()
            trip_data = response.json()["resMsg"]

            if trip_data.get("monthTripDayCnt", 0) > 0:
                result = MonthTripInfo(
                    yyyymm=yyyymm_string,
                    day_list=[],
                    summary=TripInfo(
                        drive_time=trip_data.get("tripDrvTime"),
                        idle_time=trip_data.get("tripIdleTime"),
                        distance=trip_data.get("tripDist"),
                        avg_speed=trip_data.get("tripAvgSpeed"),
                        max_speed=trip_data.get("tripMaxSpeed"),
                    ),
                )

                for day in trip_data.get("tripDayList", []):
                    processed_day = DayTripCounts(
                        yyyymmdd=day["tripDayInMonth"],
                        trip_count=day["tripCntDay"],
                    )
                    result.day_list.append(processed_day)

                vehicle.month_trip_info = result
        except Exception as e:
            _LOGGER.warning(f"{DOMAIN} - Failed to get month trip info: {e}")

    def update_day_trip_info(
        self, token: Token, vehicle: Vehicle, yyyymmdd_string: str
    ) -> None:
        """Update daily trip info."""
        url = self._build_api_url(f"/spa/vehicles/{vehicle.id}/tripinfo")
        data = {"tripPeriodType": 1, "setTripDay": yyyymmdd_string}

        headers = self._get_authenticated_headers(token)
        response = self.session.post(url, json=data, headers=headers)

        try:
            response.raise_for_status()
            trip_data = response.json()["resMsg"]
            day_trip_list = trip_data.get("dayTripList", [])

            if len(day_trip_list) > 0:
                msg = day_trip_list[0]
                result = DayTripInfo(
                    yyyymmdd=yyyymmdd_string,
                    trip_list=[],
                    summary=TripInfo(
                        drive_time=msg.get("tripDrvTime"),
                        idle_time=msg.get("tripIdleTime"),
                        distance=msg.get("tripDist"),
                        avg_speed=msg.get("tripAvgSpeed"),
                        max_speed=msg.get("tripMaxSpeed"),
                    ),
                )

                for trip in msg.get("tripList", []):
                    processed_trip = TripInfo(
                        hhmmss=trip.get("tripTime"),
                        drive_time=trip.get("tripDrvTime"),
                        idle_time=trip.get("tripIdleTime"),
                        distance=trip.get("tripDist"),
                        avg_speed=trip.get("tripAvgSpeed"),
                        max_speed=trip.get("tripMaxSpeed"),
                    )
                    result.trip_list.append(processed_trip)

                vehicle.day_trip_info = result
        except Exception as e:
            _LOGGER.warning(f"{DOMAIN} - Failed to get day trip info: {e}")
