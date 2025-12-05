"""HyundaiBlueLinkApiBR.py"""

# pylint:disable=logging-fstring-interpolation,invalid-name,broad-exception-caught,unused-argument,missing-function-docstring,line-too-long

import datetime as dt
import logging
import typing as ty
from datetime import timedelta
from time import sleep
from urllib.parse import urljoin, urlparse

import requests

from .ApiImpl import ApiImpl, ClimateRequestOptions, WindowRequestOptions
from .const import (
    BRAND_HYUNDAI,
    BRANDS,
    DISTANCE_UNITS,
    DOMAIN,
    ENGINE_TYPES,
    ORDER_STATUS,
    SEAT_STATUS,
    VEHICLE_LOCK_ACTION,
    WINDOW_STATE,
)
from .exceptions import APIError
from .Token import Token
from .utils import get_index_into_hex_temp, parse_date_br
from .Vehicle import DayTripCounts, DayTripInfo, MonthTripInfo, TripInfo, Vehicle

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
        self.api_v2_url = f"https://{self.base_url}/api/v2/"
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

    def _build_api_v2_url(self, path: str) -> str:
        """Build API v2 URL from path."""
        return urljoin(self.api_v2_url, path.lstrip("/"))

    def _get_authenticated_headers(self, token: Token) -> dict:
        """Get headers with authentication."""
        headers = dict(self.api_headers)
        device_id = token.device_id or self.ccsp_device_id
        headers["ccsp-device-id"] = device_id
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
        response = self.session.get(url, params=params)
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

    def login(
        self,
        username: str,
        password: str,
        token: Token | None = None,
        otp_handler: ty.Callable[[dict], dict] | None = None,
    ) -> Token:
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
            device_id=self.ccsp_device_id,
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

    def _ensure_control_token(self, token: Token) -> str:
        """Ensure we have a valid control token for remote commands."""
        control_token = getattr(token, "control_token", None)
        expires_at = getattr(token, "control_token_expires_at", None)
        if (
            control_token
            and expires_at
            and expires_at - dt.timedelta(seconds=5) > dt.datetime.now(dt.timezone.utc)
        ):
            return control_token

        if not token.pin:
            raise APIError("PIN is required for remote commands.")

        device_id = token.device_id or self.ccsp_device_id
        token.device_id = device_id

        url = self._build_api_url("/user/pin")
        headers = self._get_authenticated_headers(token)
        payload = {"pin": token.pin, "deviceId": device_id}

        response = self.session.put(url, json=payload, headers=headers)
        response.raise_for_status()
        data = response.json()

        if data.get("controlToken") is None:
            raise APIError("Failed to obtain control token.")

        control_token = f"Bearer {data['controlToken']}"
        expires_in = data.get("expiresTime", 0)
        expires_at = dt.datetime.now(dt.timezone.utc) + dt.timedelta(
            seconds=expires_in or 600
        )

        token.control_token = control_token
        token.control_token_expires_at = expires_at
        return control_token

    def lock_action(
        self, token: Token, vehicle: Vehicle, action: VEHICLE_LOCK_ACTION
    ) -> str:
        """Lock or unlock the vehicle."""
        control_token = self._ensure_control_token(token)
        device_id = token.device_id or self.ccsp_device_id

        url = self._build_api_v2_url(
            f"spa/vehicles/{vehicle.id}/control/door",
        )
        headers = self._get_authenticated_headers(token)
        headers["Authorization"] = control_token
        headers["ccsp-device-id"] = device_id
        headers["ccuCCS2ProtocolSupport"] = str(vehicle.ccu_ccs2_protocol_support or 0)

        payload = {"deviceId": device_id, "action": action.value}
        _LOGGER.debug(f"{DOMAIN} - Lock action request: %s", payload)

        response = self.session.post(url, json=payload, headers=headers)
        response.raise_for_status()
        data = response.json()
        _LOGGER.debug(f"{DOMAIN} - Lock action response: %s", data)

        if data.get("retCode") != "S":
            raise APIError(
                f"Lock action failed: {data.get('resCode')} {data.get('resMsg')}"
            )

        return data.get("msgId")

    def check_action_status(
        self,
        token: Token,
        vehicle: Vehicle,
        action_id: str,
        synchronous: bool = False,
        timeout: int = 0,
    ) -> ORDER_STATUS:
        """Check status of a previously submitted remote command."""
        if synchronous:
            if timeout < 1:
                raise APIError("Timeout must be 1 or higher for synchronous checks.")

            end_time = dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=timeout)
            while dt.datetime.now(dt.timezone.utc) < end_time:
                state = self.check_action_status(
                    token, vehicle, action_id, synchronous=False
                )
                if state == ORDER_STATUS.PENDING:
                    sleep(5)
                    continue
                return state

            return ORDER_STATUS.TIMEOUT

        url = self._build_api_url(f"/spa/notifications/{vehicle.id}/records")
        headers = self._get_authenticated_headers(token)

        response = self.session.get(url, headers=headers)
        response.raise_for_status()
        data = response.json()
        _LOGGER.debug(f"{DOMAIN} - Action status response: %s", data)

        records = data.get("resMsg", [])
        for record in records:
            if record.get("recordId") != action_id:
                continue

            result = (record.get("result") or "").lower()
            if result == "success":
                return ORDER_STATUS.SUCCESS
            if result == "fail":
                return ORDER_STATUS.FAILED
            if result == "non-response":
                return ORDER_STATUS.TIMEOUT
            if result in ("", "pending", None):
                return ORDER_STATUS.PENDING

        return ORDER_STATUS.UNKNOWN

    def set_windows_state(
        self, token: Token, vehicle: Vehicle, options: WindowRequestOptions
    ) -> str:
        """Open or close all windows (BR API controls all windows together)."""
        control_token = self._ensure_control_token(token)
        device_id = token.device_id or self.ccsp_device_id

        url = self._build_api_v2_url(f"spa/vehicles/{vehicle.id}/control/window")

        # Brazilian API uses simple action for all windows at once
        # Check if any window should be open, otherwise close
        action = "open"
        if options.front_left == WINDOW_STATE.CLOSED:
            action = "close"
        elif options.front_right == WINDOW_STATE.CLOSED:
            action = "close"
        elif options.back_left == WINDOW_STATE.CLOSED:
            action = "close"
        elif options.back_right == WINDOW_STATE.CLOSED:
            action = "close"

        headers = self._get_authenticated_headers(token)
        headers["Authorization"] = control_token
        headers["ccsp-device-id"] = device_id
        headers["ccuCCS2ProtocolSupport"] = str(vehicle.ccu_ccs2_protocol_support or 0)

        payload = {"action": action, "deviceId": device_id}
        _LOGGER.debug(f"{DOMAIN} - Window action request: {payload}")

        response = self.session.post(url, json=payload, headers=headers)
        response.raise_for_status()
        data = response.json()
        _LOGGER.debug(f"{DOMAIN} - Window action response: {data}")

        if data.get("retCode") != "S":
            raise APIError(
                f"Window action failed: {data.get('resCode')} {data.get('resMsg')}"
            )

        return data.get("msgId")

    def start_hazard_lights(self, token: Token, vehicle: Vehicle) -> str:
        """Turn on hazard lights (lights only, no horn)."""
        control_token = self._ensure_control_token(token)
        device_id = token.device_id or self.ccsp_device_id

        url = self._build_api_v2_url(f"spa/vehicles/{vehicle.id}/control/light")
        headers = self._get_authenticated_headers(token)
        headers["Authorization"] = control_token
        headers["ccsp-device-id"] = device_id
        headers["ccuCCS2ProtocolSupport"] = str(vehicle.ccu_ccs2_protocol_support or 0)

        _LOGGER.debug(f"{DOMAIN} - Hazard lights request")

        response = self.session.post(url, headers=headers)
        response.raise_for_status()
        data = response.json()
        _LOGGER.debug(f"{DOMAIN} - Hazard lights response: {data}")

        if data.get("retCode") != "S":
            raise APIError(
                f"Hazard lights failed: {data.get('resCode')} {data.get('resMsg')}"
            )

        return data.get("msgId")

    def get_notification_history(self, token: Token, vehicle: Vehicle) -> list:
        """Get notification history (for debugging and tracking command results)."""
        url = self._build_api_url(f"/spa/notifications/{vehicle.id}/history")
        headers = self._get_authenticated_headers(token)

        response = self.session.get(url, headers=headers)
        response.raise_for_status()
        data = response.json()
        _LOGGER.debug(f"{DOMAIN} - Notification history response")

        return data.get("resMsg", [])

    def start_climate(
        self, token: Token, vehicle: Vehicle, options: ClimateRequestOptions
    ) -> str:
        """Start climate control with temperature and seat heating settings."""
        control_token = self._ensure_control_token(token)
        device_id = token.device_id or self.ccsp_device_id

        url = self._build_api_v2_url(f"spa/vehicles/{vehicle.id}/control/engine")

        # Set defaults
        if options.set_temp is None:
            options.set_temp = 21  # 21°C default
        if options.duration is None:
            options.duration = 10  # 10 minutes default
        if options.defrost is None:
            options.defrost = False
        if options.climate is None:
            options.climate = True
        if options.heating is None:
            options.heating = 0
        if options.front_left_seat is None:
            options.front_left_seat = 0

        # Convert temperature to hex code
        # BR API uses direct Celsius value converted to hex
        temp_celsius = int(options.set_temp)
        temp_code = get_index_into_hex_temp(temp_celsius)

        # Map seat heating level (0-5 in ClimateRequestOptions to 0-8 for BR API)
        # 0=off, 1-3=heat levels, 4-5=cool levels (BR uses similar mapping)
        seat_heat_cmd = options.front_left_seat if options.front_left_seat else 0

        headers = self._get_authenticated_headers(token)
        headers["Authorization"] = control_token
        headers["ccsp-device-id"] = device_id
        headers["ccuCCS2ProtocolSupport"] = str(vehicle.ccu_ccs2_protocol_support or 0)

        payload = {
            "action": "start",
            "options": {
                "airCtrl": 1 if options.climate else 0,
                "heating1": int(options.heating),
                "seatHeaterVentCMD": {"drvSeatOptCmd": seat_heat_cmd},
                "defrost": options.defrost,
                "igniOnDuration": options.duration,
            },
            "hvacType": 1,
            "deviceId": device_id,
            "tempCode": temp_code,
            "unit": "C",
        }

        _LOGGER.debug(f"{DOMAIN} - Start climate request: {payload}")

        response = self.session.post(url, json=payload, headers=headers)
        response.raise_for_status()
        data = response.json()
        _LOGGER.debug(f"{DOMAIN} - Start climate response: {data}")

        if data.get("retCode") != "S":
            raise APIError(
                f"Start climate failed: {data.get('resCode')} {data.get('resMsg')}"
            )

        return data.get("msgId")

    def stop_climate(self, token: Token, vehicle: Vehicle) -> str:
        """Stop climate control."""
        control_token = self._ensure_control_token(token)
        device_id = token.device_id or self.ccsp_device_id

        url = self._build_api_v2_url(f"spa/vehicles/{vehicle.id}/control/engine")

        headers = self._get_authenticated_headers(token)
        headers["Authorization"] = control_token
        headers["ccsp-device-id"] = device_id
        headers["ccuCCS2ProtocolSupport"] = str(vehicle.ccu_ccs2_protocol_support or 0)

        payload = {"action": "stop", "deviceId": device_id}

        _LOGGER.debug(f"{DOMAIN} - Stop climate request: {payload}")

        response = self.session.post(url, json=payload, headers=headers)
        response.raise_for_status()
        data = response.json()
        _LOGGER.debug(f"{DOMAIN} - Stop climate response: {data}")

        if data.get("retCode") != "S":
            raise APIError(
                f"Stop climate failed: {data.get('resCode')} {data.get('resMsg')}"
            )

        return data.get("msgId")

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
