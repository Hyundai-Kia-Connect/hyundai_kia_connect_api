import json
import logging
import random
import time
import uuid
from datetime import datetime, timedelta
from urllib.parse import parse_qs, urlparse

import curlify
import push_receiver
import requests
from requests.adapters import HTTPAdapter
from requests.packages.urllib3.util.ssl_ import create_urllib3_context

from .const import (BRAND_HYUNDAI, BRANDS, DOMAIN,
                    VEHICLE_LOCK_ACTION)
from .utils import get_child_value
from .ApiImpl import ApiImpl
from .Token import Token
from .Vehicle import Vehicle

CIPHERS = "DEFAULT@SECLEVEL=1"

_LOGGER = logging.getLogger(__name__)


class cipherAdapter(HTTPAdapter):
    """
    A HTTPAdapter that re-enables poor ciphers required by Hyundai.
    """

    def init_poolmanager(self, *args, **kwargs):
        context = create_urllib3_context(ciphers=CIPHERS)
        kwargs["ssl_context"] = context
        return super().init_poolmanager(*args, **kwargs)

    def proxy_manager_for(self, *args, **kwargs):
        context = create_urllib3_context(ciphers=CIPHERS)
        kwargs["ssl_context"] = context
        return super().proxy_manager_for(*args, **kwargs)


class HyundaiBlueLinkAPIUSA(ApiImpl):

    # initialize with a timestamp which will allow the first fetch to occur
    last_loc_timestamp = datetime.now() - timedelta(hours=3)

    def __init__(
        self,
        region: int,
        brand: int,
    ):
        self.BASE_URL: str = "api.telematics.hyundaiusa.com"
        self.LOGIN_API: str = "https://" + self.BASE_URL + "/v2/ac/"
        self.API_URL: str = "https://" + self.BASE_URL + "/ac/v2/"
        self.temperature_range = range(62, 82)
        
        ts = time.time()
        utc_offset = (
            datetime.fromtimestamp(ts) - datetime.utcfromtimestamp(ts)
        ).total_seconds()
        utc_offset_hours = int(utc_offset / 60 / 60)

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
            "to": "ISS",
            "language": "0",
            "offset": str(utc_offset_hours),
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-origin",
            "refresh": "false",
            "encryptFlag": "false",
            "brandIndicator": "H",
            "gen": "2",
            "username": self.username,
            "blueLinkServicePin": self.pin,
            "client_id": "m66129Bb-em93-SPAHYN-bZ91-am4540zp19920",
            "clientSecret": "v558o935-6nne-423i-baa8",
        }
        self.sessions = requests.Session()
        self.sessions.mount("https://" + self.BASE_URL, cipherAdapter())

        _LOGGER.debug(f"{DOMAIN} - initial API headers: {self.API_HEADERS}")

    def login(self, username: str, password: str) -> Token:

        ### Sign In with Email and Password and Get Authorization Code ###

        url = self.LOGIN_API + "oauth/token"

        data = {"username": username, "password": password}
        headers = self.API_HEADERS
        response = self.sessions.post(url, json=data, headers=headers)
        _LOGGER.debug(f"{DOMAIN} - Sign In Response {response.text}")
        response = response.json()
        access_token = response["access_token"]
        refresh_token = response["refresh_token"]
        expires_in = float(response["expires_in"])
        _LOGGER.debug(f"{DOMAIN} - Access Token Value {access_token}")
        _LOGGER.debug(f"{DOMAIN} - Refresh Token Value {refresh_token}")


        valid_until = (datetime.now() + timedelta(seconds=expires_in))

        return Token(
            username=username,
            password=password,
            access_token=access_token,
            refresh_token=refresh_token,
            valid_until=valid_until,
        )  

    def _get_cached_vehicle_state(self, token: Token, vehicle: Vehicle) -> dict:
        # Vehicle Status Call
        url = self.API_URL + "rcs/rvs/vehicleStatus"
        headers = self.API_HEADERS
        headers["accessToken"] = token.access_token
        headers["vin"] = vehicle.vin

        _LOGGER.debug(f"{DOMAIN} - using API headers: {self.API_HEADERS}")

        response = self.sessions.get(url, headers=headers)
        response = response.json()
        _LOGGER.debug(f"{DOMAIN} - get_cached_vehicle_status response {response}")

        vehicle_status = {}
        vehicle_status["vehicleStatus"] = response["vehicleStatus"]

        vehicle_status["vehicleStatus"]["dateTime"] = (
            vehicle_status["vehicleStatus"]["dateTime"]
            .replace("-", "")
            .replace("T", "")
            .replace(":", "")
            .replace("Z", "")
        )
        vehicle_status["vehicleLocation"] = vehicle_status["vehicleStatus"][
            "vehicleLocation"
        ]

        # Get Odomoter Details - Needs to be refactored
        #response = self.get_vehicle(token.access_token)
        #vehicle_status["odometer"] = {}
        #vehicle_status["odometer"]["unit"] = 3
        #vehicle_status["odometer"]["value"] = response["enrolledVehicleDetails"][0][
        #    "vehicleDetails"
        #]["odometer"]

        #vehicle_status["vehicleLocation"] = self.get_location(
        #    token, vehicle_status["odometer"]["value"]
        #)
        return vehicle_status

    def update_vehicle_with_cached_state(self, token: Token, vehicle: Vehicle) -> None:
        state = self._get_cached_vehicle_state(token, vehicle)
        vehicle.last_updated_at = self.get_last_updated_at(
            get_child_value(state, "vehicleStatus.dateTime")
        )
        vehicle.total_driving_distance = (
            get_child_value(
                state,
                "vehicleStatus.evStatus.drvDistance.0.rangeByFuel.totalAvailableRange.value",
            ),
            "km",
        )
        vehicle.odometer = (
            get_child_value(state, "odometer.value"),
            "km",
        )
        vehicle.car_battery_percentage = get_child_value(
            state, "vehicleStatus.battery.batSoc"
        )
        vehicle.engine_is_running = get_child_value(state, "vehicleStatus.engine")
        vehicle.air_temperature = (
            get_child_value(state, "vehicleStatus.evStatus.airTemp.value"),
            "c",
        )
        vehicle.defrost_is_on = get_child_value(state, "vehicleStatus.defrost")
        vehicle.steering_wheel_heater_is_on = get_child_value(
            state, "vehicleStatus.steerWheelHeat"
        )
        vehicle.back_window_heater_is_on = get_child_value(
            state, "vehicleStatus.sideBackWindowHeat"
        )
        vehicle.side_mirror_heater_is_on = get_child_value(
            state, "vehicleStatus.sideMirrorHeat"
        )
        vehicle.front_left_seat_heater_is_on = get_child_value(
            state, "vehicleStatus.seatHeaterVentState.flSeatHeatState"
        )
        vehicle.front_right_seat_heater_is_on = get_child_value(
            state, "vehicleStatus.seatHeaterVentState.frSeatHeatState"
        )
        vehicle.rear_left_seat_heater_is_on = get_child_value(
            state, "vehicleStatus.seatHeaterVentState.rlSeatHeatState"
        )
        vehicle.rear_right_seat_heater_is_on = get_child_value(
            state, "vehicleStatus.seatHeaterVentState.rrSeatHeatState"
        )
        vehicle.tire_pressure_rear_left_warning_is_on = get_child_value(
            state, "vehicleStatus.tirePressureLamp.tirePressureWarningLampRearLeft"
        )
        vehicle.tire_pressure_front_left_warning_is_on = get_child_value(
            state, "vehicleStatus.tirePressureLamp.tirePressureWarningLampFrontLeft"
        )
        vehicle.tire_pressure_front_right_warning_is_on = get_child_value(
            state, "vehicleStatus.tirePressureLamp.tirePressureWarningLampFrontRight"
        )
        vehicle.tire_pressure_rear_right_warning_is_on = get_child_value(
            state, "vehicleStatus.tirePressureLamp.tirePressureWarningLampRearRight"
        )
        vehicle.tire_pressure_all_warning_is_on = get_child_value(
            state, "vehicleStatus.tirePressureLamp.tirePressureWarningLampAll"
        )
        vehicle.is_locked = (not get_child_value(state, "vehicleStatus.doorLockStatus"))
        vehicle.front_left_door_is_open = get_child_value(
            state, "vehicleStatus.doorOpen.frontLeft"
        )
        vehicle.front_right_door_is_open = get_child_value(
            state, "vehicleStatus.doorOpen.frontRight"
        )
        vehicle.back_left_door_is_open = get_child_value(
            state, "vehicleStatus.doorOpen.backLeft"
        )
        vehicle.back_right_door_is_open = get_child_value(
            state, "vehicleStatus.doorOpen.backRight"
        )
        vehicle.trunk_is_open = get_child_value(state, "vehicleStatus.trunkOpen")
        vehicle.ev_battery_percentage = get_child_value(
            state, "vehicleStatus.evStatus.batteryStatus"
        )
        vehicle.ev_battery_is_charging = get_child_value(
            state, "vehicleStatus.evStatus.batteryCharge"
        )
        vehicle.ev_battery_is_plugged_in = get_child_value(
            state, "vehicleStatus.evStatus.batteryPlugin"
        )
        vehicle.ev_driving_distance = (
            get_child_value(
                state,
                "vehicleStatus.evStatus.drvDistance.0.rangeByFuel.evModeRange.value",
            ),
            "km",
        )
        vehicle.ev_estimated_current_charge_duration = (
            get_child_value(state, "vehicleStatus.evStatus.remainTime2.atc.value"),
            "m",
        )
        vehicle.ev_estimated_fast_charge_duration = (
            get_child_value(state, "vehicleStatus.evStatus.remainTime2.etc1.value"),
            "m",
        )
        vehicle.ev_estimated_portable_charge_duration = (
            get_child_value(state, "vehicleStatus.evStatus.remainTime2.etc2.value"),
            "m",
        )
        vehicle.ev_estimated_station_charge_duration = (
            get_child_value(state, "vehicleStatus.evStatus.remainTime2.etc3.value"),
            "m",
        )
        vehicle.fuel_driving_distance = (
            get_child_value(
                state,
                "vehicleStatus.evStatus.drvDistance.0.rangeByFuel.gasModeRange.value",
            ),
            "km",
        )
        vehicle.fuel_level_is_low = get_child_value(state, "vehicleStatus.lowFuelLight")
        vehicle.data = state
        
    def get_location(self, token: Token, vehicle: Vehicle) -> None:
        r"""
        Get the location of the vehicle

        Only update the location if the odometer moved AND if the last location update was over an hour ago.
        Note that the "last updated" time is initially set to three hours ago.

        This will help to prevent too many cals to the API
        """
        url = self.API_URL + "rcs/rfc/findMyCar"
        headers = self.API_HEADERS
        headers["accessToken"] = token.access_token
        headers["vehicleId"] = vehicle.id
        #Not used, not sure why it is here? 
        #headers["pAuth"] = self.get_pin_token(token)
        
        try:
            HyundaiBlueLinkAPIUSA.last_loc_timestamp = datetime.now()
            response = self.sessions.get(url, headers=headers)
            response_json = response.json()
            _LOGGER.debug(f"{DOMAIN} - Get Vehicle Location {response_json}")
            if response_json.get("coord") is not None:
                return response_json
            else:
                # Check for rate limit exceeded
                # These hard-coded values were extracted from a rate limit exceeded response.  In either case the log
                # will include the full response when the "coord" attribute is not present
                if (
                    response_json.get("errorCode", 0) == 502
                    and response_json.get("errorSubCode", "") == "HT_534"
                ):
                    # rate limit exceeded; set the last_loc_timestamp such that the next check will be at least 12 hours from now
                    HyundaiBlueLinkAPIUSA.last_loc_timestamp = (
                        datetime.now() + timedelta(hours=11)
                    )
                    _LOGGER.warn(
                        f"{DOMAIN} - get vehicle location rate limit exceeded.  Location will not be fetched until at least {HyundaiBlueLinkAPIUSA.last_loc_timestamp + timedelta(hours = 12)}"
                    )
                else:
                    _LOGGER.warn(
                        f"{DOMAIN} - Unable to get vehicle location: {response_json}"
                    )

                if HyundaiBlueLinkAPIUSA.old_vehicle_status is not None:
                    return HyundaiBlueLinkAPIUSA.old_vehicle_status.get(
                        "vehicleLocation"
                    )
                else:
                    return None

        except Exception as e:
            _LOGGER.warning(
                f"{DOMAIN} - Get vehicle location failed: {e}", exc_info=True
            )
            if HyundaiBlueLinkAPIUSA.old_vehicle_status is not None:
                return HyundaiBlueLinkAPIUSA.old_vehicle_status.get(
                    "vehicleLocation"
                )
            else:
                return None

    def get_vehicles(self, token: Token):
        url = self.API_URL + "enrollment/details/" + token.username
        headers = self.API_HEADERS
        headers["accessToken"] = token.access_token
        response = self.sessions.get(url, headers=headers)
        _LOGGER.debug(f"{DOMAIN} - Get Vehicles Response {response.text}")
        response = response.json()
        result = []
        for entry in response["enrolledVehicleDetails"]:
            entry = entry["vehicleDetails"]
            vehicle: Vehicle = Vehicle(
                id=entry["regid"],
                name=entry["nickName"],
                VIN=entry["vin"],
                model=entry["modelCode"],
                registration_date=["enrollmentDate"],
            )
            result.append(vehicle)

        return result

    def get_pin_token(self, token: Token):
        pass

    def update_vehicle_status(self, token: Token):
        pass

    def lock_action(self, token: Token, vehicle: Vehicle, action) -> None:
        _LOGGER.debug(f"{DOMAIN} - Action for lock is: {action}")

        if action == "close":
            url = self.API_URL + "rcs/rdo/off"
            _LOGGER.debug(f"{DOMAIN} - Calling Lock")
        else:
            url = self.API_URL + "rcs/rdo/on"
            _LOGGER.debug(f"{DOMAIN} - Calling unlock")

        headers = self.API_HEADERS
        headers["accessToken"] = token.access_token
        headers["vin"] = vehicle.vin
        headers["registrationId"] = vehicle.id
        headers["APPCLOUD-VIN"] = vehicle.vin

        data = {"userName": self.username, "vin": vehicle.vin}
        response = self.sessions.post(url, headers=headers, json=data)
        # response_headers = response.headers
        # response = response.json()
        # action_status = self.check_action_status(token, headers["pAuth"], response_headers["transactionId"])

        # _LOGGER.debug(f"{DOMAIN} - Received lock_action response {action_status}")
        _LOGGER.debug(
            f"{DOMAIN} - Received lock_action response status code: {response.status_code}"
        )
        _LOGGER.debug(f"{DOMAIN} - Received lock_action response: {response.text}")

    def start_climate(
        self, token: Token, vehicle: Vehicle, set_temp, duration, defrost, climate, heating
    ) -> None:
        _LOGGER.debug(f"{DOMAIN} - Start engine..")

        url = self.API_URL + "rcs/rsc/start"

        headers = self.API_HEADERS
        headers["accessToken"] = token.access_token
        headers["vin"] = vehicle.vin
        headers["registrationId"] = vehicle.id
        _LOGGER.debug(f"{DOMAIN} - Start engine headers: {headers}")

        data = {
            "Ims": 0,
            "airCtrl": int(climate),
            "airTemp": {"unit": 1, "value": set_temp},
            "defrost": defrost,
            "heating1": int(heating),
            "igniOnDuration": duration,
            # "seatHeaterVentInfo": None,
            "username": self.username,
            "vin": vehicle.id,
        }
        _LOGGER.debug(f"{DOMAIN} - Start engine data: {data}")

        response = self.sessions.post(url, json=data, headers=headers)

        # _LOGGER.debug(f"{DOMAIN} - Start engine curl: {curlify.to_curl(response.request)}")
        _LOGGER.debug(
            f"{DOMAIN} - Start engine response status code: {response.status_code}"
        )
        _LOGGER.debug(f"{DOMAIN} - Start engine response: {response.text}")

    def stop_climate(self, token: Token, vehicle: Vehicle) -> None:
        _LOGGER.debug(f"{DOMAIN} - Stop engine..")

        url = self.API_URL + "rcs/rsc/stop"

        headers = self.API_HEADERS
        headers["accessToken"] = token.access_token
        headers["vin"] = vehicle.vin
        headers["registrationId"] = vehicle.id

        _LOGGER.debug(f"{DOMAIN} - Stop engine headers: {headers}")

        response = self.sessions.post(url, headers=headers)
        _LOGGER.debug(
            f"{DOMAIN} - Stop engine response status code: {response.status_code}"
        )
        _LOGGER.debug(f"{DOMAIN} - Stop engine response: {response.text}")

    def start_charge(self, token: Token, vehicle: Vehicle) -> None:
        pass

    def stop_charge(self, token: Token, vehicle: Vehicle) -> None:
        pass
