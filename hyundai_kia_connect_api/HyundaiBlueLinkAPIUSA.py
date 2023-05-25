"""HyundaiBlueLinkAPIUSA.py"""
# pylint:disable=logging-fstring-interpolation,deprecated-method,invalid-name,broad-exception-caught,unused-argument,missing-function-docstring

import logging
import time
import re
import datetime as dt
import pytz
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.ssl_ import create_urllib3_context

from .const import (
    DOMAIN,
    VEHICLE_LOCK_ACTION,
    SEAT_STATUS,
    DISTANCE_UNITS,
    TEMPERATURE_UNITS,
)
from .utils import get_child_value
from .ApiImpl import ApiImpl, ClimateRequestOptions
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
    """HyundaiBlueLinkAPIUSA"""

    # initialize with a timestamp which will allow the first fetch to occur
    last_loc_timestamp = dt.datetime.now(pytz.utc) - dt.timedelta(hours=3)

    def __init__(self, region: int, brand: int, language: str):
        self.LANGUAGE: str = language
        self.BASE_URL: str = "api.telematics.hyundaiusa.com"
        self.LOGIN_API: str = "https://" + self.BASE_URL + "/v2/ac/"
        self.API_URL: str = "https://" + self.BASE_URL + "/ac/v2/"
        self.temperature_range = range(62, 82)

        ts = time.time()
        utc_offset = (
            dt.datetime.fromtimestamp(ts) - dt.datetime.utcfromtimestamp(ts)
        ).total_seconds()
        utc_offset_hours = int(utc_offset / 60 / 60)

        origin: str = "https://" + self.BASE_URL
        referer: str = origin + "/login"

        self.API_HEADERS = {
            "content-type": "application/json;charset=UTF-8",
            "accept": "application/json, text/plain, */*",
            "accept-encoding": "gzip, deflate, br",
            "accept-language": "en-US,en;q=0.9",
            "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/75.0.3770.142 Safari/537.36",  # noqa
            "host": self.BASE_URL,
            "origin": origin,
            "referer": referer,
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
            "client_id": "m66129Bb-em93-SPAHYN-bZ91-am4540zp19920",
            "clientSecret": "v558o935-6nne-423i-baa8",
        }
        self.sessions = requests.Session()
        self.sessions.mount(origin, cipherAdapter())

        _LOGGER.debug(f"{DOMAIN} - initial API headers: {self.API_HEADERS}")

    def _get_authenticated_headers(self, token: Token) -> dict:
        headers = dict(self.API_HEADERS)
        headers["username"] = token.username
        headers["accessToken"] = token.access_token
        headers["blueLinkServicePin"] = token.pin
        return headers

    def _get_vehicle_headers(self, token: Token, vehicle: Vehicle) -> dict:
        headers = self._get_authenticated_headers(token)
        headers["registrationId"] = vehicle.id
        headers["vin"] = vehicle.VIN
        return headers

    def login(self, username: str, password: str) -> Token:
        # Sign In with Email and Password and Get Authorization Code
        url = self.LOGIN_API + "oauth/token"
        data = {"username": username, "password": password}

        response = self.sessions.post(url, json=data, headers=self.API_HEADERS)
        _LOGGER.debug(f"{DOMAIN} - Sign In Response {response.text}")
        response = response.json()
        access_token = response["access_token"]
        refresh_token = response["refresh_token"]
        expires_in = float(response["expires_in"])
        _LOGGER.debug(f"{DOMAIN} - Access Token Value {access_token}")
        _LOGGER.debug(f"{DOMAIN} - Refresh Token Value {refresh_token}")

        valid_until = dt.datetime.now(pytz.utc) + dt.timedelta(seconds=expires_in)

        return Token(
            username=username,
            password=password,
            access_token=access_token,
            refresh_token=refresh_token,
            valid_until=valid_until,
        )

    def _get_vehicle_details(self, token: Token, vehicle: Vehicle):
        url = self.API_URL + "enrollment/details/" + token.username
        headers = self._get_authenticated_headers(token)
        response = self.sessions.get(url, headers=headers)
        _LOGGER.debug(f"{DOMAIN} - Get Vehicles Response {response.text}")
        response = response.json()
        for entry in response["enrolledVehicleDetails"]:
            entry = entry["vehicleDetails"]
            if entry["regid"] == vehicle.id:
                return entry

    def _get_vehicle_status(
        self, token: Token, vehicle: Vehicle, refresh: bool
    ) -> dict:
        # Vehicle Status Call
        url = self.API_URL + "rcs/rvs/vehicleStatus"
        headers = self._get_vehicle_headers(token, vehicle)
        if refresh:
            headers["REFRESH"] = "true"

        _LOGGER.debug(f"{DOMAIN} - using API headers: {headers}")

        response = self.sessions.get(url, headers=headers)
        response = response.json()
        _LOGGER.debug(f"{DOMAIN} - get_vehicle_status response {response}")

        status = dict(response["vehicleStatus"])

        status["dateTime"] = (
            status["dateTime"]
            .replace("-", "")
            .replace("T", "")
            .replace(":", "")
            .replace("Z", "")
        )

        return status

    def _get_vehicle_location(self, token: Token, vehicle: Vehicle):
        """
        Get the location of the vehicle
        This logic only checks odometer move in the update.
        This call doesn't protect from overlimit as per:
        Only update the location if the odometer moved AND if the last location
        update was over an hour ago.
        Note that the "last updated" time is initially set to three hours ago.
        This will help to prevent too many calls to the API
        """
        url = self.API_URL + "rcs/rfc/findMyCar"
        headers = self._get_vehicle_headers(token, vehicle)
        try:
            response = self.sessions.get(url, headers=headers)
            response_json = response.json()
            _LOGGER.debug(f"{DOMAIN} - Get Vehicle Location {response_json}")
            if response_json.get("coord") is not None:
                return response_json
            else:
                if (
                    response_json.get("errorCode", 0) == 502
                    and response_json.get("errorSubCode", "") == "HT_534"
                ):
                    _LOGGER.warn(
                        f"{DOMAIN} - get vehicle location rate limit exceeded."
                    )
                else:
                    _LOGGER.warn(
                        f"{DOMAIN} - Unable to get vehicle location: {response_json}"
                    )

        except Exception as e:
            _LOGGER.warning(
                f"{DOMAIN} - Get vehicle location failed: {e}", exc_info=True
            )

        _LOGGER.debug(f"{DOMAIN} - Get Vehicle Location result is None")
        return None

    def _update_vehicle_properties(self, vehicle: Vehicle, state: dict) -> None:
        vehicle.last_updated_at = self.get_last_updated_at(
            get_child_value(state, "vehicleStatus.dateTime")
        )
        vehicle.total_driving_range = (
            get_child_value(
                state,
                "vehicleStatus.evStatus.drvDistance.0.rangeByFuel.totalAvailableRange.value",  # noqa
            ),
            DISTANCE_UNITS[
                get_child_value(
                    state,
                    "vehicleStatus.evStatus.drvDistance.0.rangeByFuel.totalAvailableRange.unit",  # noqa
                )
            ],
        )
        if get_child_value(
            state,
            "vehicleStatus.dte.value",
        ):
            vehicle.fuel_driving_range = (
                get_child_value(
                    state,
                    "vehicleStatus.dte.value",
                ),
                DISTANCE_UNITS[
                    get_child_value(
                        state,
                        "vehicleStatus.dte.unit",
                    )
                ],
            )
        vehicle.odometer = (
            get_child_value(state, "vehicleDetails.odometer"),
            DISTANCE_UNITS[3],
        )
        vehicle.car_battery_percentage = get_child_value(
            state, "vehicleStatus.battery.batSoc"
        )
        vehicle.engine_is_running = get_child_value(state, "vehicleStatus.engine")
        vehicle.washer_fluid_warning_is_on = get_child_value(
            state, "vehicleStatus.washerFluidStatus"
        )
        vehicle.smart_key_battery_warning_is_on = get_child_value(
            state, "vehicleStatus.smartKeyBatteryWarning"
        )

        air_temp = get_child_value(state, "vehicleStatus.airTemp.value")

        if air_temp == "LO":
            air_temp = self.temperature_range[0]
        if air_temp == "HI":
            air_temp = self.temperature_range[-1]
        if air_temp:
            vehicle.air_temperature = (air_temp, TEMPERATURE_UNITS[1])
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
        vehicle.front_left_seat_status = SEAT_STATUS[
            get_child_value(state, "vehicleStatus.seatHeaterVentState.flSeatHeatState")
        ]
        vehicle.front_right_seat_status = SEAT_STATUS[
            get_child_value(state, "vehicleStatus.seatHeaterVentState.frSeatHeatState")
        ]
        vehicle.rear_left_seat_status = SEAT_STATUS[
            get_child_value(state, "vehicleStatus.seatHeaterVentState.rlSeatHeatState")
        ]
        vehicle.rear_right_seat_status = SEAT_STATUS[
            get_child_value(state, "vehicleStatus.seatHeaterVentState.rrSeatHeatState")
        ]
        vehicle.tire_pressure_rear_left_warning_is_on = bool(
            get_child_value(
                state, "vehicleStatus.tirePressureLamp.tirePressureWarningLampRearLeft"
            )
        )
        vehicle.tire_pressure_front_left_warning_is_on = bool(
            get_child_value(
                state, "vehicleStatus.tirePressureLamp.tirePressureWarningLampFrontLeft"
            )
        )
        vehicle.tire_pressure_front_right_warning_is_on = bool(
            get_child_value(
                state,
                "vehicleStatus.tirePressureLamp.tirePressureWarningLampFrontRight",
            )
        )
        vehicle.tire_pressure_rear_right_warning_is_on = bool(
            get_child_value(
                state, "vehicleStatus.tirePressureLamp.tirePressureWarningLampRearRight"
            )
        )
        vehicle.tire_pressure_all_warning_is_on = bool(
            get_child_value(
                state, "vehicleStatus.tirePressureLamp.tirePressureWarningLampAll"
            )
        )
        vehicle.front_left_window_is_open = get_child_value(
            state, "vehicleStatus.windowOpen.frontLeft"
        )
        vehicle.front_right_window_is_open = get_child_value(
            state, "vehicleStatus.windowOpen.frontRight"
        )
        vehicle.back_left_window_is_open = get_child_value(
            state, "vehicleStatus.windowOpen.backLeft"
        )
        vehicle.back_right_window_is_open = get_child_value(
            state, "vehicleStatus.windowOpen.backRight"
        )
        vehicle.is_locked = get_child_value(state, "vehicleStatus.doorLock")
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
        vehicle.hood_is_open = get_child_value(state, "vehicleStatus.hoodOpen")
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
        vehicle.ev_driving_range = (
            get_child_value(
                state,
                "vehicleStatus.evStatus.drvDistance.0.rangeByFuel.evModeRange.value",
            ),
            DISTANCE_UNITS[
                get_child_value(
                    state,
                    "vehicleStatus.evStatus.drvDistance.0.rangeByFuel.evModeRange.unit",
                )
            ],
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
        if get_child_value(
            state,
            "vehicleStatus.evStatus.drvDistance.0.rangeByFuel.gasModeRange.value",
        ):
            vehicle.fuel_driving_range = (
                get_child_value(
                    state,
                    "vehicleStatus.evStatus.drvDistance.0.rangeByFuel.gasModeRange.value",  # noqa
                ),
                DISTANCE_UNITS[
                    get_child_value(
                        state,
                        "vehicleStatus.evStatus.drvDistance.0.rangeByFuel.gasModeRange.unit",  # noqa
                    )
                ],
            )
        vehicle.fuel_level_is_low = get_child_value(state, "vehicleStatus.lowFuelLight")

        vehicle.fuel_level = get_child_value(state, "vehicleStatus.fuelLevel")

        if get_child_value(state, "vehicleStatus.vehicleLocation.coord.lat"):
            vehicle.location = (
                get_child_value(state, "vehicleStatus.vehicleLocation.coord.lat"),
                get_child_value(state, "vehicleStatus.vehicleLocation.coord.lon"),
                self.get_last_updated_at(
                    get_child_value(state, "vehicleStatus.vehicleLocation.time")
                ),
            )
        vehicle.air_control_is_on = get_child_value(state, "vehicleStatus.airCtrlOn")

        vehicle.data = state

    def update_vehicle_with_cached_state(self, token: Token, vehicle: Vehicle) -> None:
        state = {}
        state["vehicleDetails"] = self._get_vehicle_details(token, vehicle)
        state["vehicleStatus"] = self._get_vehicle_status(token, vehicle, False)

        if state["vehicleStatus"] is not None:
            vehicle_location_result = None
            if vehicle.odometer:
                if vehicle.odometer < get_child_value(
                    state["vehicleDetails"], "odometer"
                ):
                    vehicle_location_result = self._get_vehicle_location(token, vehicle)
                else:
                    cached_location = state["vehicleStatus"]["vehicleLocation"]
                    _LOGGER.debug(
                        f"{DOMAIN} - update_vehicle_with_cached_state keep Location fallback {cached_location}"  # noqa
                    )
            else:
                vehicle_location_result = self._get_vehicle_location(token, vehicle)

            if vehicle_location_result is not None:
                state["vehicleStatus"]["vehicleLocation"] = vehicle_location_result
            else:
                cached_location = state["vehicleStatus"]["vehicleLocation"]
                _LOGGER.debug(
                    f"{DOMAIN} - update_vehicle_with_cached_state Location fallback {cached_location}"  # noqa
                )

        self._update_vehicle_properties(vehicle, state)

    def force_refresh_vehicle_state(self, token: Token, vehicle: Vehicle) -> None:
        state = {}
        state["vehicleDetails"] = self._get_vehicle_details(token, vehicle)
        state["vehicleStatus"] = self._get_vehicle_status(token, vehicle, True)
        if state["vehicleStatus"] is not None:
            vehicle_location_result = self._get_vehicle_location(token, vehicle)
            if vehicle_location_result is not None:
                state["vehicleStatus"]["vehicleLocation"] = vehicle_location_result
            else:
                cached_location = state["vehicleStatus"]["vehicleLocation"]
                _LOGGER.debug(
                    f"{DOMAIN} - force_refresh_vehicle_state Location fallback {cached_location}"  # noqa
                )

        self._update_vehicle_properties(vehicle, state)

    def get_vehicles(self, token: Token):
        url = self.API_URL + "enrollment/details/" + token.username
        headers = self._get_authenticated_headers(token)
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
                timezone=self.data_timezone,
            )
            result.append(vehicle)

        return result

    def lock_action(self, token: Token, vehicle: Vehicle, action) -> None:
        _LOGGER.debug(f"{DOMAIN} - Action for lock is: {action}")

        if action == VEHICLE_LOCK_ACTION.LOCK:
            url = self.API_URL + "rcs/rdo/off"
            _LOGGER.debug(f"{DOMAIN} - Calling Lock")
        elif action == VEHICLE_LOCK_ACTION.UNLOCK:
            url = self.API_URL + "rcs/rdo/on"
            _LOGGER.debug(f"{DOMAIN} - Calling unlock")

        headers = self._get_vehicle_headers(token, vehicle)
        headers["APPCLOUD-VIN"] = vehicle.VIN

        data = {"userName": token.username, "vin": vehicle.VIN}
        response = self.sessions.post(url, headers=headers, json=data)
        # response_headers = response.headers
        # response = response.json()
        # action_status = self.check_action_status(token, headers["pAuth"], response_headers["transactionId"])  # noqa

        # _LOGGER.debug(f"{DOMAIN} - Received lock_action response {action_status}")
        _LOGGER.debug(
            f"{DOMAIN} - Received lock_action response status code: {response.status_code}"  # noqa
        )
        _LOGGER.debug(f"{DOMAIN} - Received lock_action response: {response.text}")

    def start_climate(
        self, token: Token, vehicle: Vehicle, options: ClimateRequestOptions
    ) -> str:
        _LOGGER.debug(f"{DOMAIN} - Start engine..")

        url = self.API_URL + "rcs/rsc/start"

        headers = self._get_vehicle_headers(token, vehicle)
        _LOGGER.debug(f"{DOMAIN} - Start engine headers: {headers}")

        if options.climate is None:
            options.climate = True
        if options.set_temp is None:
            options.set_temp = 70
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

        data = {
            "Ims": 0,
            "airCtrl": int(options.climate),
            "airTemp": {"unit": 1, "value": options.set_temp},
            "defrost": options.defrost,
            "heating1": int(options.heating),
            "igniOnDuration": options.duration,
            "seatHeaterVentInfo": {
                "drvSeatHeatState": options.front_left_seat,
                "astSeatHeatState": options.front_right_seat,
                "rlSeatHeatState": options.rear_left_seat,
                "rrSeatHeatState": options.rear_right_seat,
            },
            "username": token.username,
            "vin": vehicle.id,
        }
        _LOGGER.debug(f"{DOMAIN} - Start engine data: {data}")

        response = self.sessions.post(url, json=data, headers=headers)
        _LOGGER.debug(
            f"{DOMAIN} - Start engine response status code: {response.status_code}"
        )
        _LOGGER.debug(f"{DOMAIN} - Start engine response: {response.text}")

    def stop_climate(self, token: Token, vehicle: Vehicle) -> None:
        _LOGGER.debug(f"{DOMAIN} - Stop engine..")

        url = self.API_URL + "rcs/rsc/stop"

        headers = self._get_vehicle_headers(token, vehicle)

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

    def get_last_updated_at(self, value) -> dt.datetime:
        _LOGGER.debug(f"{DOMAIN} - last_updated_at - before {value}")
        if value is None:
            value = dt.datetime(2000, 1, 1, tzinfo=self.data_timezone)
        else:
            value = (
                value.replace("-", "")
                .replace("T", "")
                .replace(":", "")
                .replace("Z", "")
            )
            m = re.match(r"(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})", value)
            value = dt.datetime(
                year=int(m.group(1)),
                month=int(m.group(2)),
                day=int(m.group(3)),
                hour=int(m.group(4)),
                minute=int(m.group(5)),
                second=int(m.group(6)),
                tzinfo=self.data_timezone,
            )

        _LOGGER.debug(f"{DOMAIN} - last_updated_at - after {value}")
        return value
