"""KiaUvoApiCA.py"""

# pylint:disable=unused-argument,missing-timeout,logging-fstring-interpolation,bare-except,invalid-name,missing-function-docstring

import time
import datetime as dt
import json
import logging

import pytz
import requests

from dateutil.tz import tzoffset

from .ApiImpl import ApiImpl, ClimateRequestOptions
from .Token import Token
from .Vehicle import Vehicle
from .const import (
    BRAND_GENESIS,
    BRAND_HYUNDAI,
    BRAND_KIA,
    BRANDS,
    DISTANCE_UNITS,
    DOMAIN,
    ENGINE_TYPES,
    LOGIN_TOKEN_LIFETIME,
    OrderStatus,
    SEAT_STATUS,
    TEMPERATURE_UNITS,
    VEHICLE_LOCK_ACTION,
)

from .exceptions import AuthenticationError, APIError
from .utils import (
    get_child_value,
    get_hex_temp_into_index,
    get_index_into_hex_temp,
    parse_datetime,
)

import ssl
import urllib3

CIPHERS = "ALL:@SECLEVEL=0"

_LOGGER = logging.getLogger(__name__)


class cipherAdapter(requests.adapters.HTTPAdapter):
    """
    A HTTPAdapter that re-enables poor ciphers required by Hyundai.
    """

    def init_poolmanager(self, connections, maxsize, block=False):
        context = ssl.create_default_context()
        context.set_ciphers(CIPHERS)
        context.options |= getattr(ssl, "OP_LEGACY_SERVER_CONNECT", 0x4)
        self.poolmanager = urllib3.poolmanager.PoolManager(
            ssl_version=ssl.PROTOCOL_TLS, ssl_context=context
        )


class KiaUvoApiCA(ApiImpl):
    """KiaUvoApiCA"""

    temperature_range_c_old = [x * 0.5 for x in range(32, 64)]
    temperature_range_c_new = [x * 0.5 for x in range(28, 64)]
    temperature_range_model_year = 2020

    def __init__(self, region: int, brand: int, language: str) -> None:
        self.vehicle_timezone = self.data_timezone
        self.LANGUAGE: str = language
        if BRANDS[brand] == BRAND_KIA:
            self.BASE_URL: str = "kiaconnect.ca"
        elif BRANDS[brand] == BRAND_HYUNDAI:
            self.BASE_URL: str = "mybluelink.ca"
        elif BRANDS[brand] == BRAND_GENESIS:
            self.BASE_URL: str = "genesisconnect.ca"

        self.old_vehicle_status = {}
        self.API_URL: str = "https://" + self.BASE_URL + "/tods/api/"
        self.API_HEADERS = {
            "content-type": "application/json;charset=UTF-8",
            "accept": "application/json, text/plain, */*",
            "accept-encoding": "gzip, deflate, br",
            "accept-language": "en-US,en;q=0.9",
            "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/75.0.3770.142 Safari/537.36",  # noqa
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
        self.sessions = requests.Session()
        self.sessions.mount("https://" + self.BASE_URL, cipherAdapter())

    def _check_response_for_errors(self, response: dict) -> None:
        """
        Checks for errors in the API response.
        If an error is found, an exception is raised.
        retCode known values:
        - S: success
        - F: failure
        resCode / resMsg known values:
        - 0000: no error
        - 7404: "Wrong Username and password"
        - 7402: "Account Locked Out"
        :param response: the API's JSON response
        """

        error_code_mapping = {"7404": AuthenticationError, "7402": AuthenticationError}
        if response["responseHeader"]["responseCode"] == 1:
            if response["error"]["errorCode"] in error_code_mapping:
                raise error_code_mapping[response["error"]["errorCode"]](
                    response["error"]["errorDesc"]
                )
            else:
                raise APIError(f"Server returned: '{response['error']['errorDesc']}'")

    def login(self, username: str, password: str) -> Token:
        # Sign In with Email and Password and Get Authorization Code
        url = self.API_URL + "lgn"
        data = {"loginId": username, "password": password}
        headers = self.API_HEADERS
        response = self.sessions.post(url, json=data, headers=headers)
        _LOGGER.debug(f"{DOMAIN} - Sign In Response {response.text}")
        response = response.json()
        self._check_response_for_errors(response)
        response = response["result"]
        access_token = response["accessToken"]
        refresh_token = response["refreshToken"]
        _LOGGER.debug(f"{DOMAIN} - Access Token Value {access_token}")
        _LOGGER.debug(f"{DOMAIN} - Refresh Token Value {refresh_token}")

        valid_until = dt.datetime.now(pytz.utc) + LOGIN_TOKEN_LIFETIME

        return Token(
            username=username,
            password=password,
            access_token=access_token,
            refresh_token=refresh_token,
            valid_until=valid_until,
        )

    def get_vehicles(self, token: Token) -> list[Vehicle]:
        url = self.API_URL + "vhcllst"
        headers = self.API_HEADERS
        headers["accessToken"] = token.access_token
        response = self.sessions.post(url, headers=headers)
        _LOGGER.debug(f"{DOMAIN} - Get Vehicles Response {response.text}")
        response = response.json()
        result = []
        for entry in response["result"]["vehicles"]:
            entry_engine_type = None
            if entry["fuelKindCode"] == "G":
                entry_engine_type = ENGINE_TYPES.ICE
            elif entry["fuelKindCode"] == "E":
                entry_engine_type = ENGINE_TYPES.EV
            elif entry["fuelKindCode"] == "P":
                entry_engine_type = ENGINE_TYPES.PHEV
            vehicle: Vehicle = Vehicle(
                id=entry["vehicleId"],
                name=entry["nickName"],
                model=entry["modelName"],
                year=int(entry["modelYear"]),
                VIN=entry["vin"],
                engine_type=entry_engine_type,
                timezone=self.data_timezone,
                dtc_count=entry["dtcCount"],
            )
            result.append(vehicle)
        return result

    def update_vehicle_with_cached_state(self, token: Token, vehicle: Vehicle) -> None:
        state = self._get_cached_vehicle_state(token, vehicle)
        self._update_vehicle_properties_base(vehicle, state)

        # Service Status Call
        service = self._get_next_service(token, vehicle)

        # Get location if the car has moved since last call
        if vehicle.odometer:
            if vehicle.odometer < get_child_value(service, "currentOdometer"):
                location = self.get_location(token, vehicle)
                self._update_vehicle_properties_location(vehicle, location)
        else:
            location = self.get_location(token, vehicle)
            self._update_vehicle_properties_location(vehicle, location)

        # Update service after the fact so we still have the old odometer
        # reading available for above.
        self._update_vehicle_properties_service(vehicle, service)
        if vehicle.engine_type == ENGINE_TYPES.EV:
            charge = self._get_charge_limits(token, vehicle)
            self._update_vehicle_properties_charge(vehicle, charge)

    def force_refresh_vehicle_state(self, token: Token, vehicle: Vehicle) -> None:
        state = self._get_forced_vehicle_state(token, vehicle)

        # Calculate offset between vehicle last_updated_at and UTC
        self.vehicle_timezone = vehicle.timezone
        last_updated_at = parse_datetime(
            get_child_value(state, "status.lastStatusDate"), self.data_timezone
        )
        now_utc: dt = dt.datetime.now(pytz.utc)
        offset = round((last_updated_at - now_utc).total_seconds() / 3600)
        _LOGGER.debug(f"{DOMAIN} - Offset between vehicle and UTC: {offset} hours")
        if offset != 0:
            # Set our timezone to account for the offset
            vehicle.timezone = tzoffset("VEHICLETIME", offset * 3600)
            _LOGGER.debug(f"{DOMAIN} - Set vehicle.timezone to UTC + {offset} hours")

        self._update_vehicle_properties_base(vehicle, state)

        # Service Status Call
        service = self._get_next_service(token, vehicle)

        # Get location if the car has moved since last call
        if vehicle.odometer:
            if vehicle.odometer < get_child_value(service, "currentOdometer"):
                location = self.get_location(token, vehicle)
                self._update_vehicle_properties_location(vehicle, location)
        else:
            location = self.get_location(token, vehicle)
            self._update_vehicle_properties_location(vehicle, location)

        # Update service after the fact so we still have the old odometer
        # reading available for above.
        self._update_vehicle_properties_service(vehicle, service)

        if vehicle.engine_type == ENGINE_TYPES.EV:
            charge = self._get_charge_limits(token, vehicle)
            self._update_vehicle_properties_charge(vehicle, charge)

    def _update_vehicle_properties_base(self, vehicle: Vehicle, state: dict) -> None:
        _LOGGER.debug(f"{DOMAIN} - Old Vehicle Last Updated: {vehicle.last_updated_at}")
        self.vehicle_timezone = vehicle.timezone
        vehicle.last_updated_at = parse_datetime(
            get_child_value(state, "status.lastStatusDate"), self.data_timezone
        )
        _LOGGER.debug(
            f"{DOMAIN} - Current Vehicle Last Updated: {vehicle.last_updated_at}"
        )
        # Converts temp to usable number. Currently only support celsius.
        # Future to do is check unit in case the care itself is set to F.
        if (
            get_child_value(state, "status.airTemp.value") != "OFF"
            and get_child_value(state, "status.airTemp.value")[-1] == "H"
        ):
            tempIndex = get_hex_temp_into_index(
                get_child_value(state, "status.airTemp.value")
            )
            if get_child_value(state, "status.airTemp.unit") == 0:
                if vehicle.year >= self.temperature_range_model_year:
                    state["status"]["airTemp"]["value"] = self.temperature_range_c_new[
                        tempIndex
                    ]

                else:
                    state["status"]["airTemp"]["value"] = self.temperature_range_c_old[
                        tempIndex
                    ]

        vehicle.total_driving_range = (
            get_child_value(
                state,
                "status.evStatus.drvDistance.0.rangeByFuel.totalAvailableRange.value",
            ),
            DISTANCE_UNITS[
                get_child_value(
                    state,
                    "status.evStatus.drvDistance.0.rangeByFuel.totalAvailableRange.unit",  # noqa
                )
            ],
        )

        vehicle.car_battery_percentage = get_child_value(state, "status.battery.batSoc")
        vehicle.engine_is_running = get_child_value(state, "status.engine")
        vehicle.washer_fluid_warning_is_on = get_child_value(
            state, "status.washerFluidStatus"
        )
        vehicle.brake_fluid_warning_is_on = get_child_value(
            state, "status.breakOilStatus"
        )
        vehicle.tire_pressure_rear_left_warning_is_on = bool(
            get_child_value(state, "status.tirePressureLamp.tirePressureLampRL")
        )
        vehicle.tire_pressure_front_left_warning_is_on = bool(
            get_child_value(state, "status.tirePressureLamp.tirePressureLampFL")
        )
        vehicle.tire_pressure_front_right_warning_is_on = bool(
            get_child_value(state, "status.tirePressureLamp.tirePressureLampFR")
        )
        vehicle.tire_pressure_rear_right_warning_is_on = bool(
            get_child_value(state, "status.tirePressureLamp.tirePressureLampRR")
        )
        vehicle.tire_pressure_all_warning_is_on = bool(
            get_child_value(state, "status.tirePressureLamp.tirePressureLampAll")
        )
        vehicle.air_temperature = (
            get_child_value(state, "status.airTemp.value"),
            TEMPERATURE_UNITS[0],
        )
        vehicle.defrost_is_on = get_child_value(state, "status.defrost")
        vehicle.steering_wheel_heater_is_on = get_child_value(
            state, "status.steerWheelHeat"
        )
        vehicle.back_window_heater_is_on = get_child_value(
            state, "status.sideBackWindowHeat"
        )
        vehicle.side_mirror_heater_is_on = get_child_value(
            state, "status.sideMirrorHeat"
        )
        vehicle.front_left_seat_status = SEAT_STATUS[
            get_child_value(state, "status.seatHeaterVentState.flSeatHeatState")
        ]
        vehicle.front_right_seat_status = SEAT_STATUS[
            get_child_value(state, "status.seatHeaterVentState.frSeatHeatState")
        ]
        vehicle.rear_left_seat_status = SEAT_STATUS[
            get_child_value(state, "status.seatHeaterVentState.rlSeatHeatState")
        ]
        vehicle.rear_right_seat_status = SEAT_STATUS[
            get_child_value(state, "status.seatHeaterVentState.rrSeatHeatState")
        ]
        vehicle.is_locked = get_child_value(state, "status.doorLock")
        vehicle.front_left_door_is_open = get_child_value(
            state, "status.doorOpen.frontLeft"
        )
        vehicle.front_right_door_is_open = get_child_value(
            state, "status.doorOpen.frontRight"
        )
        vehicle.back_left_door_is_open = get_child_value(
            state, "status.doorOpen.backLeft"
        )
        vehicle.back_right_door_is_open = get_child_value(
            state, "status.doorOpen.backRight"
        )
        vehicle.hood_is_open = get_child_value(state, "status.hoodOpen")

        vehicle.trunk_is_open = get_child_value(state, "status.trunkOpen")
        vehicle.front_left_window_is_open = get_child_value(
            state, "status.windowOpen.frontLeft"
        )
        vehicle.front_right_window_is_open = get_child_value(
            state, "status.windowOpen.frontRight"
        )
        vehicle.back_left_window_is_open = get_child_value(
            state, "status.windowOpen.backLeft"
        )
        vehicle.back_right_window_is_open = get_child_value(
            state, "status.windowOpen.backRight"
        )
        vehicle.ev_battery_percentage = get_child_value(
            state, "status.evStatus.batteryStatus"
        )
        vehicle.ev_battery_is_charging = get_child_value(
            state, "status.evStatus.batteryCharge"
        )
        vehicle.ev_battery_is_plugged_in = get_child_value(
            state, "status.evStatus.batteryPlugin"
        )
        vehicle.ev_driving_range = (
            get_child_value(
                state,
                "status.evStatus.drvDistance.0.rangeByFuel.evModeRange.value",
            ),
            DISTANCE_UNITS[
                get_child_value(
                    state,
                    "status.evStatus.drvDistance.0.rangeByFuel.evModeRange.unit",
                )
            ],
        )
        vehicle.ev_estimated_current_charge_duration = (
            get_child_value(state, "status.evStatus.remainTime2.atc.value"),
            "m",
        )
        vehicle.ev_estimated_fast_charge_duration = (
            get_child_value(state, "status.evStatus.remainTime2.etc1.value"),
            "m",
        )
        vehicle.ev_estimated_portable_charge_duration = (
            get_child_value(state, "status.evStatus.remainTime2.etc2.value"),
            "m",
        )
        vehicle.ev_estimated_station_charge_duration = (
            get_child_value(state, "status.evStatus.remainTime2.etc3.value"),
            "m",
        )
        vehicle.fuel_driving_range = (
            get_child_value(
                state,
                "status.dte.value",
            ),
            DISTANCE_UNITS[get_child_value(state, "status.dte.unit")],
        )
        vehicle.fuel_level_is_low = get_child_value(state, "status.lowFuelLight")
        vehicle.fuel_level = get_child_value(state, "status.fuelLevel")
        vehicle.air_control_is_on = get_child_value(state, "status.airCtrlOn")
        if vehicle.data is None:
            vehicle.data = {}
        vehicle.data["status"] = state["status"]

    def _update_vehicle_properties_service(self, vehicle: Vehicle, state: dict) -> None:
        vehicle.odometer = (
            get_child_value(state, "currentOdometer"),
            DISTANCE_UNITS[get_child_value(state, "currentOdometerUnit")],
        )
        vehicle.next_service_distance = (
            get_child_value(state, "imatServiceOdometer"),
            DISTANCE_UNITS[get_child_value(state, "imatServiceOdometerUnit")],
        )
        vehicle.last_service_distance = (
            get_child_value(state, "msopServiceOdometer"),
            DISTANCE_UNITS[get_child_value(state, "msopServiceOdometerUnit")],
        )

        vehicle.data["service"] = state

    def _update_vehicle_properties_location(
        self, vehicle: Vehicle, state: dict
    ) -> None:
        if get_child_value(state, "coord.lat"):
            self.vehicle_timezone = vehicle.timezone
            vehicle.location = (
                get_child_value(state, "coord.lat"),
                get_child_value(state, "coord.lon"),
                parse_datetime(get_child_value(state, "time"), self.data_timezone),
            )
        vehicle.data["vehicleLocation"] = state

    def _get_cached_vehicle_state(self, token: Token, vehicle: Vehicle) -> dict:
        # Vehicle Status Call
        url = self.API_URL + "lstvhclsts"
        headers = self.API_HEADERS
        headers["accessToken"] = token.access_token
        headers["vehicleId"] = vehicle.id

        response = self.sessions.post(url, headers=headers)
        response = response.json()
        _LOGGER.debug(f"{DOMAIN} - get_cached_vehicle_status response {response}")
        response = response["result"]["status"]

        status = {}
        status["status"] = response

        return status

    def _get_forced_vehicle_state(self, token: Token, vehicle: Vehicle) -> dict:
        url = self.API_URL + "rltmvhclsts"
        headers = self.API_HEADERS
        headers["accessToken"] = token.access_token
        headers["vehicleId"] = vehicle.id

        response = self.sessions.post(url, headers=headers)
        response = response.json()

        _LOGGER.debug(f"{DOMAIN} - Received forced vehicle data {response}")
        response = response["result"]["status"]
        status = {}
        status["status"] = response

        return status

    def _get_next_service(self, token: Token, vehicle: Vehicle) -> dict:
        headers = self.API_HEADERS
        headers["accessToken"] = token.access_token
        headers["vehicleId"] = vehicle.id
        url = self.API_URL + "nxtsvc"
        response = self.sessions.post(url, headers=headers)
        response = response.json()
        _LOGGER.debug(f"{DOMAIN} - Get Service status data {response}")
        response = response["result"]["maintenanceInfo"]
        return response

    def get_location(self, token: Token, vehicle: Vehicle) -> dict:
        url = self.API_URL + "fndmcr"
        headers = self.API_HEADERS
        headers["accessToken"] = token.access_token
        headers["vehicleId"] = vehicle.id
        try:
            headers["pAuth"] = self._get_pin_token(token, vehicle)

            response = self.sessions.post(
                url, headers=headers, data=json.dumps({"pin": token.pin})
            )
            response = response.json()
            _LOGGER.debug(f"{DOMAIN} - Get Vehicle Location {response}")
            if response["responseHeader"]["responseCode"] != 0:
                raise APIError("No Location Located")
            return response["result"]
        except Exception:
            _LOGGER.warning(f"{DOMAIN} - Get vehicle location failed")
            return None

    def _get_pin_token(self, token: Token, vehicle: Vehicle) -> None:
        url = self.API_URL + "vrfypin"
        headers = self.API_HEADERS
        headers["accessToken"] = token.access_token
        headers["vehicleId"] = vehicle.id

        response = self.sessions.post(
            url, headers=headers, data=json.dumps({"pin": token.pin})
        )
        _LOGGER.debug(f"{DOMAIN} - Received Pin validation response {response.json()}")
        result = response.json()["result"]

        return result["pAuth"]

    def lock_action(self, token: Token, vehicle: Vehicle, action) -> str:
        _LOGGER.debug(f"{DOMAIN} - Action for lock is: {action}")
        if action == VEHICLE_LOCK_ACTION.LOCK:
            url = self.API_URL + "drlck"
            _LOGGER.debug(f"{DOMAIN} - Calling Lock")
        elif action == VEHICLE_LOCK_ACTION.UNLOCK:
            url = self.API_URL + "drulck"
            _LOGGER.debug(f"{DOMAIN} - Calling unlock")
        headers = self.API_HEADERS
        headers["accessToken"] = token.access_token
        headers["vehicleId"] = vehicle.id
        headers["pAuth"] = self._get_pin_token(token, vehicle)

        response = self.sessions.post(
            url, headers=headers, data=json.dumps({"pin": token.pin})
        )
        response_headers = response.headers
        response = response.json()

        _LOGGER.debug(f"{DOMAIN} - Received lock_action response {response}")
        return response_headers["transactionId"]

    def start_climate(
        self, token: Token, vehicle: Vehicle, options: ClimateRequestOptions
    ) -> str:
        if vehicle.engine_type == ENGINE_TYPES.EV:
            url = self.API_URL + "evc/rfon"
        else:
            url = self.API_URL + "rmtstrt"
        headers = self.API_HEADERS
        headers["accessToken"] = token.access_token
        headers["vehicleId"] = vehicle.id
        headers["pAuth"] = self._get_pin_token(token, vehicle)

        if options.climate is None:
            options.climate = True
        if options.set_temp is None:
            options.set_temp = 21
        if options.duration is None:
            options.duration = 5
        if options.heating is None:
            options.heating = 0
        if options.defrost is None:
            options.defrost = False
        if options.front_left_seat is None:
            options.front_left_seat = 0
        if options.front_right_seat is None:
            options.front_right_seat = 0
        if options.rear_left_seat is None:
            options.rear_left_seat = 0
        if options.rear_right_seat is None:
            options.rear_right_seat = 0

        if vehicle.year >= self.temperature_range_model_year:
            hex_set_temp = get_index_into_hex_temp(
                self.temperature_range_c_new.index(options.set_temp)
            )
        else:
            hex_set_temp = get_index_into_hex_temp(
                self.temperature_range_c_old.index(options.set_temp)
            )
        if vehicle.engine_type == ENGINE_TYPES.EV:
            payload = {
                "hvacInfo": {
                    "airCtrl": int(options.climate),
                    "defrost": options.defrost,
                    "heating1": options.heating,
                    "airTemp": {
                        "value": hex_set_temp,
                        "unit": 0,
                        "hvacTempType": 1,
                    },
                },
                "pin": token.pin,
            }
        else:
            payload = {
                "setting": {
                    "airCtrl": int(options.climate),
                    "defrost": options.defrost,
                    "heating1": options.heating,
                    "igniOnDuration": options.duration,
                    "ims": 0,
                    "airTemp": {"value": hex_set_temp, "unit": 0, "hvacTempType": 0},
                    "seatHeaterVentCMD": {
                        "drvSeatOptCmd": options.front_left_seat,
                        "astSeatOptCmd": options.front_right_seat,
                        "rlSeatOptCmd": options.rear_left_seat,
                        "rrSeatOptCmd": options.rear_right_seat,
                    },
                },
                "pin": token.pin,
            }
        _LOGGER.debug(f"{DOMAIN} - Planned start_climate payload {payload}")

        response = self.sessions.post(url, headers=headers, data=json.dumps(payload))
        response_headers = response.headers
        response = response.json()

        _LOGGER.debug(f"{DOMAIN} - Received start_climate response {response}")
        return response_headers["transactionId"]

    def stop_climate(self, token: Token, vehicle: Vehicle) -> str:
        if vehicle.engine_type == ENGINE_TYPES.EV:
            url = self.API_URL + "evc/rfoff"
        else:
            url = self.API_URL + "rmtstp"
        headers = self.API_HEADERS
        headers["accessToken"] = token.access_token
        headers["vehicleId"] = vehicle.id
        headers["pAuth"] = self._get_pin_token(token, vehicle)

        response = self.sessions.post(
            url, headers=headers, data=json.dumps({"pin": token.pin})
        )
        response_headers = response.headers
        response = response.json()

        _LOGGER.debug(f"{DOMAIN} - Received stop_climate response: {response}")
        return response_headers["transactionId"]

    def check_action_status(
        self,
        token: Token,
        vehicle: Vehicle,
        action_id: str,
        synchronous: bool = False,
        timeout: int = 0,
    ) -> OrderStatus:
        if timeout < 0:
            return OrderStatus.TIMEOUT
        start_time = dt.datetime.now()

        url = self.API_URL + "rmtsts"
        headers = self.API_HEADERS
        headers["accessToken"] = token.access_token
        headers["vehicleId"] = vehicle.id
        headers["transactionId"] = action_id
        headers["pAuth"] = self._get_pin_token(token, vehicle)
        response = self.sessions.post(url, headers=headers)
        response = response.json()

        last_action_completed = (
            response["result"]["transaction"]["apiStatusCode"] != "null"
        )

        if last_action_completed:
            action_status = response["result"]["transaction"]["apiStatusCode"]
            _LOGGER.debug(f"{DOMAIN} - Last action_status: {action_status}")

        if response["responseHeader"]["responseCode"] == 1:
            return OrderStatus.FAILED
        elif response["result"]["transaction"]["apiResult"] == "C":
            return OrderStatus.SUCCESS
        elif response["result"]["transaction"]["apiResult"] == "P":
            if not synchronous:
                return OrderStatus.PENDING
            else:
                timedelta = dt.datetime.now() - start_time
                time_left = timeout - timedelta.seconds - 10
                time.sleep(10)
                return self.check_action_status(
                    token, vehicle, action_id, synchronous, time_left
                )

        return OrderStatus.FAILED

    def start_charge(self, token: Token, vehicle: Vehicle) -> str:
        url = self.API_URL + "evc/rcstrt"
        headers = self.API_HEADERS
        headers["accessToken"] = token.access_token
        headers["vehicleId"] = vehicle.id
        headers["pAuth"] = self._get_pin_token(token, vehicle)
        _LOGGER.debug(f"{DOMAIN} - Planned start_charge headers {headers}")
        data = json.dumps({"pin": token.pin})
        _LOGGER.debug(f"{DOMAIN} - Planned start_charge payload {data}")
        response = self.sessions.post(
            url, headers=headers, data=json.dumps({"pin": token.pin})
        )
        response_headers = response.headers
        response = response.json()

        _LOGGER.debug(f"{DOMAIN} - Received start_charge response {response}")
        return response_headers["transactionId"]

    def stop_charge(self, token: Token, vehicle: Vehicle) -> str:
        url = self.API_URL + "evc/rcstp"
        headers = self.API_HEADERS
        headers["accessToken"] = token.access_token
        headers["vehicleId"] = vehicle.id
        headers["pAuth"] = self._get_pin_token(token, vehicle)

        response = self.sessions.post(
            url, headers=headers, data=json.dumps({"pin": token.pin})
        )
        response_headers = response.headers
        response = response.json()

        _LOGGER.debug(f"{DOMAIN} - Received stop_charge response {response}")
        return response_headers["transactionId"]

    def _update_vehicle_properties_charge(self, vehicle: Vehicle, state: dict) -> None:
        try:
            if [x["level"] for x in state if x["plugType"] == 1][-1] <= 100:
                vehicle.ev_charge_limits_ac = [
                    x["level"] for x in state if x["plugType"] == 1
                ][-1]
            if [x["level"] for x in state if x["plugType"] == 0][-1] <= 100:
                vehicle.ev_charge_limits_dc = [
                    x["level"] for x in state if x["plugType"] == 0
                ][-1]
        except Exception:
            _LOGGER.debug(f"{DOMAIN} - SOC Levels couldn't be found. May not be an EV.")

    def _get_charge_limits(self, token: Token, vehicle: Vehicle) -> dict:
        url = self.API_URL + "evc/selsoc"
        headers = self.API_HEADERS
        headers["accessToken"] = token.access_token
        headers["vehicleId"] = vehicle.id

        response = self.sessions.post(url, headers=headers)
        response = response.json()
        _LOGGER.debug(f"{DOMAIN} - Received get_charge_limits: {response}")

        return response["result"]

    def set_charge_limits(
        self, token: Token, vehicle: Vehicle, ac: int, dc: int
    ) -> str:
        url = self.API_URL + "evc/setsoc"
        headers = self.API_HEADERS
        headers["accessToken"] = token.access_token
        headers["vehicleId"] = vehicle.id
        headers["pAuth"] = self._get_pin_token(token, vehicle)

        payload = {
            "tsoc": [
                {
                    "plugType": 0,
                    "level": dc,
                },
                {
                    "plugType": 1,
                    "level": ac,
                },
            ],
            "pin": token.pin,
        }

        response = self.sessions.post(url, headers=headers, data=json.dumps(payload))
        response_headers = response.headers
        response = response.json()

        _LOGGER.debug(f"{DOMAIN} - Received set_charge_limits response {response}")
        return response_headers["transactionId"]
