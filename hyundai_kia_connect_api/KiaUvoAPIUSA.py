import logging
import random
import secrets
import string
import time
from datetime import datetime, timedelta
import datetime as dt
import re

import pytz
import requests
from requests import RequestException, Response

from .const import (
    DOMAIN,
    VEHICLE_LOCK_ACTION,
    TEMPERATURE_UNITS,
    DISTANCE_UNITS,
)
from .ApiImpl import ApiImpl, ClimateRequestOptions
from .Token import Token
from .Vehicle import Vehicle
from .utils import get_child_value

_LOGGER = logging.getLogger(__name__)


class AuthError(RequestException):
    pass


def request_with_active_session(func):
    def request_with_active_session_wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except AuthError:
            _LOGGER.debug(f"{DOMAIN} - got invalid session, attempting to repair and resend")
            self = args[0]
            token = kwargs["token"]
            vehicle = kwargs["vehicle"]
            new_token = self.login(token.username, token.password)
            _LOGGER.debug(
                f"old token:{token.access_token}, new token:{new_token.access_token}"
            )
            token.access_token = new_token.access_token
            token.valid_until = new_token.valid_until
            json_body = kwargs.get("json_body", None)
            if json_body is not None and json_body.get("vinKey", None):
                json_body["vinKey"] = [vehicle.key]
            response = func(*args, **kwargs)
            return response

    return request_with_active_session_wrapper


def request_with_logging(func):
    def request_with_logging_wrapper(*args, **kwargs):
        url = kwargs["url"]
        json_body = kwargs.get("json_body")
        if json_body is not None:
            _LOGGER.debug(f"{DOMAIN} - sending {url} request with {json_body}")
        else:
            _LOGGER.debug(f"{DOMAIN} - sending {url} request")
        response = func(*args, **kwargs)
        _LOGGER.debug(f"{DOMAIN} got response {response.text}")
        response_json = response.json()
        if response_json["status"]["statusCode"] == 0:
            return response
        if (
            response_json["status"]["statusCode"] == 1
            and response_json["status"]["errorType"] == 1
            and response_json["status"]["errorCode"] == 1003
        ):
            _LOGGER.debug(f"{DOMAIN} - error: session invalid")
            raise AuthError
        _LOGGER.error(f"{DOMAIN} - error: unknown error response {response.text}")
        raise RequestException

    return request_with_logging_wrapper


class KiaUvoAPIUSA(ApiImpl):
    def __init__(
        self,
        region: int,
        brand: int,
        language
    ) -> None:
        self.LANGUAGE: str = language
        self.temperature_range = range(62, 82)

        # Randomly generate a plausible device id on startup
        self.device_id = (
            "".join(
                random.choice(string.ascii_letters + string.digits) for _ in range(22)
            )
            + ":"
            + secrets.token_urlsafe(105)
        )

        self.BASE_URL: str = "api.owners.kia.com"
        self.API_URL: str = "https://" + self.BASE_URL + "/apigw/v1/"

    def api_headers(self) -> dict:
        offset = time.localtime().tm_gmtoff / 60 / 60
        headers = {
            "content-type": "application/json;charset=UTF-8",
            "accept": "application/json, text/plain, */*",
            "accept-encoding": "gzip, deflate, br",
            "accept-language": "en-US,en;q=0.9",
            "apptype": "L",
            "appversion": "4.10.0",
            "clientid": "MWAMOBILE",
            "from": "SPA",
            "host": self.BASE_URL,
            "language": "0",
            "offset": str(int(offset)),
            "ostype": "Android",
            "osversion": "11",
            "secretkey": "98er-w34rf-ibf3-3f6h",
            "to": "APIGW",
            "tokentype": "G",
            "user-agent": "okhttp/3.12.1",
        }
        # should produce something like "Mon, 18 Oct 2021 07:06:26 GMT". May require adjusting locale to en_US
        date = datetime.now(tz=pytz.utc).strftime("%a, %d %b %Y %H:%M:%S GMT")
        headers["date"] = date
        headers["deviceid"] = self.device_id
        return headers

    def authed_api_headers(self, token: Token, vehicle: Vehicle):
        headers = self.api_headers()
        headers["sid"] = token.access_token
        headers["vinkey"] = vehicle.key
        return headers

    @request_with_active_session
    @request_with_logging
    def post_request_with_logging_and_active_session(
        self, token: Token, url: str, json_body: dict, vehicle: Vehicle
    ) -> Response:
        headers = self.authed_api_headers(token, vehicle)
        return requests.post(url, json=json_body, headers=headers)

    @request_with_active_session
    @request_with_logging
    def get_request_with_logging_and_active_session(
        self, token: Token, url: str, vehicle: Vehicle
    ) -> Response:
        headers = self.authed_api_headers(token, vehicle)
        return requests.get(url, headers=headers)

    def login(self, username: str, password: str) -> Token:
        """Login into cloud endpoints and return Token"""

        url = self.API_URL + "prof/authUser"

        data = {
            "deviceKey": "",
            "deviceType": 2,
            "userCredential": {"userId": username, "password": password},
        }
        headers = self.api_headers()
        response = requests.post(url, json=data, headers=headers)
        _LOGGER.debug(f"{DOMAIN} - Sign In Response {response.text}")
        session_id = response.headers.get("sid")
        if not session_id:
            raise Exception(
                f"no session id returned in login. Response: {response.text} headers {response.headers} cookies {response.cookies}"
            )
        _LOGGER.debug(f"got session id {session_id}")
        valid_until = dt.datetime.now(pytz.utc) + dt.timedelta(hours=1)
        return Token(
            username=username,
            password=password,
            access_token=session_id,
            valid_until=valid_until,
        )

    def get_vehicles(self, token: Token) -> list[Vehicle]:
        """Return all Vehicle instances for a given Token"""
        url = self.API_URL + "ownr/gvl"
        headers = self.api_headers()
        headers["sid"] = token.access_token
        response = requests.get(url, headers=headers)
        _LOGGER.debug(f"{DOMAIN} - Get Vehicles Response {response.text}")
        response = response.json()
        result = []
        for entry in response["payload"]["vehicleSummary"]:
            vehicle: Vehicle = Vehicle(
                id=entry["vehicleIdentifier"],
                name=entry["nickName"],
                model=entry["modelName"],
                key=entry["vehicleKey"],
            )
            result.append(vehicle)
        return result

    def refresh_vehicles(self, token: Token, vehicles: list[Vehicle]) -> None:
        """Refresh the vehicle data provided in get_vehicles. Required for Kia USA as key is session specific"""
        url = self.API_URL + "ownr/gvl"
        headers = self.api_headers()
        headers["sid"] = token.access_token
        response = requests.get(url, headers=headers)
        _LOGGER.debug(f"{DOMAIN} - Get Vehicles Response {response.text}")
        response = response.json()
        for entry in response["payload"]["vehicleSummary"]:
            if vehicles[entry["vehicleIdentifier"]]:
                vehicles[entry["vehicleIdentifier"]].name=entry["nickName"]
                vehicles[entry["vehicleIdentifier"]].model=entry["modelName"]
                vehicles[entry["vehicleIdentifier"]].key=entry["vehicleKey"]
            else:
                vehicle: Vehicle = Vehicle(
                    id=entry["vehicleIdentifier"],
                    name=entry["nickName"],
                    model=entry["modelName"],
                    key=entry["vehicleKey"],
                )
                vehicles.append(vehicle)


    def update_vehicle_with_cached_state(self, token: Token, vehicle: Vehicle) -> None:
        state = self._get_cached_vehicle_state(token, vehicle)
        self._update_vehicle_properties(vehicle, state)

    def force_refresh_vehicle_state(self, token: Token, vehicle: Vehicle) -> None:
        self._get_forced_vehicle_state(token, vehicle)
        #TODO: Force update needs work to return the correct data for processing
        #self._update_vehicle_properties(vehicle, state)
        #Temp call a cached state since we are removing this from parent logic in other commits should be removed when the above is fixed
        self.update_vehicle_with_cached_state(token, vehicle)

    def _update_vehicle_properties(self, vehicle: Vehicle, state: dict) -> None:
        """Get cached vehicle data and update Vehicle instance with it"""
        vehicle.last_updated_at = self.get_last_updated_at(
            get_child_value(state, "lastVehicleInfo.vehicleStatusRpt.vehicleStatus.syncDate.utc")
        )
        vehicle.total_driving_range = (
            get_child_value(
                state,
                "lastVehicleInfo.vehicleStatusRpt.vehicleStatus.evStatus.drvDistance.0.rangeByFuel.totalAvailableRange.value",
            ),
            DISTANCE_UNITS[3],
        )
        vehicle.odometer = (
            get_child_value(state, "vehicleConfig.vehicleDetail.vehicle.mileage"),
            DISTANCE_UNITS[3],
        )
        vehicle.next_service_distance = (
            get_child_value(state, "service.imatServiceOdometer"),
            DISTANCE_UNITS[3],
        )
        vehicle.last_service_distance = (
            get_child_value(state, "service.msopServiceOdometer"),
            DISTANCE_UNITS[3],
        )
        vehicle.car_battery_percentage = get_child_value(
            state, "lastVehicleInfo.vehicleStatusRpt.vehicleStatus.batteryStatus.stateOfCharge"
        )
        vehicle.engine_is_running = get_child_value(state, "lastVehicleInfo.vehicleStatusRpt.vehicleStatus.engine")
        vehicle.air_temperature = (
            get_child_value(state, "lastVehicleInfo.vehicleStatusRpt.vehicleStatus.climate.airTemp.value"),
            TEMPERATURE_UNITS[get_child_value(state, "lastVehicleInfo.vehicleStatusRpt.vehicleStatus.climate.airTemp.unit")],
        )
        vehicle.defrost_is_on = get_child_value(state, "lastVehicleInfo.vehicleStatusRpt.vehicleStatus.climate.defrost")
        vehicle.washer_fluid_warning_is_on = get_child_value(state, "lastVehicleInfo.vehicleStatusRpt.vehicleStatus.washerFluidStatus")
        vehicle.smart_key_battery_warning_is_on = get_child_value(state, "lastVehicleInfo.vehicleStatusRpt.vehicleStatus.smartKeyBatteryWarning")
        vehicle.tire_pressure_all_warning_is_on = get_child_value(state, "lastVehicleInfo.vehicleStatusRpt.vehicleStatus.tirePressure.all")

        vehicle.steering_wheel_heater_is_on = get_child_value(
            state, "lastVehicleInfo.vehicleStatusRpt.vehicleStatus.climate.heatingAccessory.steeringWheel"
        )
        vehicle.back_window_heater_is_on = get_child_value(
            state, "lastVehicleInfo.vehicleStatusRpt.vehicleStatus.climate.heatingAccessory.rearWindow"
        )
        vehicle.side_mirror_heater_is_on = get_child_value(
            state, "lastVehicleInfo.vehicleStatusRpt.vehicleStatus.climate.heatingAccessory.sideMirror"
        )
        vehicle.front_left_seat_heater_is_on = get_child_value(
            state, "lastVehicleInfo.vehicleStatusRpt.vehicleStatus.seatHeaterVentState.flSeatHeatState"
        )
        vehicle.front_right_seat_heater_is_on = get_child_value(
            state, "lastVehicleInfo.vehicleStatusRpt.vehicleStatus.seatHeaterVentState.frSeatHeatState"
        )
        vehicle.rear_left_seat_heater_is_on = get_child_value(
            state, "lastVehicleInfo.vehicleStatusRpt.vehicleStatus.seatHeaterVentState.rlSeatHeatState"
        )
        vehicle.rear_right_seat_heater_is_on = get_child_value(
            state, "lastVehicleInfo.vehicleStatusRpt.vehicleStatus.seatHeaterVentState.rrSeatHeatState"
        )
        vehicle.is_locked = get_child_value(state, "vehicleStatus.doorLock")
        vehicle.front_left_door_is_open = get_child_value(
            state, "lastVehicleInfo.vehicleStatusRpt.vehicleStatus.doorStatus.frontLeft"
        )
        vehicle.front_right_door_is_open = get_child_value(
            state, "lastVehicleInfo.vehicleStatusRpt.vehicleStatus.doorStatus.frontRight"
        )
        vehicle.back_left_door_is_open = get_child_value(
            state, "lastVehicleInfo.vehicleStatusRpt.vehicleStatus.doorStatus.backLeft"
        )
        vehicle.back_right_door_is_open = get_child_value(
            state, "lastVehicleInfo.vehicleStatusRpt.vehicleStatus.doorStatus.backRight"
        )
        vehicle.hood_is_open = get_child_value(state, "lastVehicleInfo.vehicleStatusRpt.vehicleStatus.doorStatus.hood")

        vehicle.trunk_is_open = get_child_value(state, "lastVehicleInfo.vehicleStatusRpt.vehicleStatus.doorStatus.trunk")
        vehicle.ev_battery_percentage = get_child_value(
            state, "lastVehicleInfo.vehicleStatusRpt.vehicleStatus.evStatus.batteryStatus"
        )
        vehicle.ev_battery_is_charging = get_child_value(
            state, "lastVehicleInfo.vehicleStatus.evStatus.batteryCharge"
        )
        vehicle.ev_battery_is_plugged_in = get_child_value(
            state, "lastVehicleInfo.vehicleStatusRpt.vehicleStatus.evStatus.batteryPlugin"
        )
        vehicle.ev_driving_distance = (
            get_child_value(
                state,
                "lastVehicleInfo.vehicleStatusRpt.vehicleStatus.evStatus.drvDistance.0.rangeByFuel.evModeRange.value",
            ),
            DISTANCE_UNITS[get_child_value(
                state,
                "lastVehicleInfo.vehicleStatusRpt.vehicleStatus.evStatus.drvDistance.0.rangeByFuel.evModeRange.unit",
            )],
        )
        vehicle.ev_estimated_current_charge_duration = (
            get_child_value(state, "lastVehicleInfo.vehicleStatusRpt.vehicleStatus.evStatus.remainChargeTime.0.timeInterval.value"),
            "m",
        )
        vehicle.ev_estimated_fast_charge_duration = (
            get_child_value(state, "lastVehicleInfo.vehicleStatusRpt.vehicleStatus.evStatus.remainChargeTime.0.etc1.value"),
            "m",
        )
        vehicle.ev_estimated_portable_charge_duration = (
            get_child_value(state, "lastVehicleInfo.vehicleStatusRpt.vehicleStatus.evStatus.remainChargeTime.0.etc2.value"),
            "m",
        )
        vehicle.ev_estimated_station_charge_duration = (
            get_child_value(state, "lastVehicleInfo.vehicleStatusRpt.vehicleStatus.evStatus.remainChargeTime.0.etc3.value"),
            "m",
        )

        vehicle.fuel_driving_range = (
            get_child_value(
                state,
                "lastVehicleInfo.vehicleStatusRpt.vehicleStatus.distanceToEmpty.value",
            ),
            DISTANCE_UNITS[get_child_value(
                state,
                "lastVehicleInfo.vehicleStatusRpt.vehicleStatus.distanceToEmpty.unit",
            )],
        )
        vehicle.fuel_level_is_low = get_child_value(state, "lastVehicleInfo.vehicleStatusRpt.vehicleStatus.lowFuelLight")
        vehicle.fuel_level = get_child_value(state, "lastVehicleInfo.vehicleStatusRpt.vehicleStatus.fuelLevel")
        vehicle.air_control_is_on = get_child_value(state, "lastVehicleInfo.vehicleStatusRpt.vehicleStatus.climate.airCtrlOn")

        vehicle.location = (
            get_child_value(state, "lastVehicleInfo.location.coord.lat"),
            get_child_value(state, "lastVehicleInfo.location.coord.lon"),
            get_child_value(state, "lastVehicleInfo.location.syncDate.utc"),

        )

        vehicle.next_service_distance = (
            get_child_value(state, "vehicleConfig.maintenance.nextServiceMile"),
            DISTANCE_UNITS[3],
        )

        vehicle.dtc_count = get_child_value(state, "lastVehicleInfo.activeDTC.dtcActiveCount")
        vehicle.dtc_descriptions = get_child_value(state, "lastVehicleInfo.activeDTC.dtcCategory")

        vehicle.data = state

    def get_last_updated_at(self, value) -> dt.datetime:
        m = re.match(r"(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})", value)
        _LOGGER.debug(f"{DOMAIN} - last_updated_at - before {value}")
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


    def _get_cached_vehicle_state(self, token: Token, vehicle: Vehicle) -> dict:
        url = self.API_URL + "cmm/gvi"

        body = {
            "vehicleConfigReq": {
                "airTempRange": "0",
                "maintenance": "1",
                "seatHeatCoolOption": "0",
                "vehicle": "1",
                "vehicleFeature": "0",
            },
            "vehicleInfoReq": {
                "drivingActivty": "0",
                "dtc": "1",
                "enrollment": "1",
                "functionalCards": "0",
                "location": "1",
                "vehicleStatus": "1",
                "weather": "0",
            },
            "vinKey": [vehicle.key],
        }
        response = self.post_request_with_logging_and_active_session(
            token=token, url=url, json_body=body, vehicle=vehicle
        )

        response_body = response.json()
        return response_body["payload"]["vehicleInfoList"][0]

    def get_location(self, token: Token, vehicle_id: str) -> None:
        pass


    def _get_forced_vehicle_state(self, token: Token, vehicle: Vehicle) -> dict:
        url = self.API_URL + "rems/rvs"
        body = {
            "requestType": 0  # value of 1 would return cached results instead of forcing update
        }
        response = self.post_request_with_logging_and_active_session(
            token=token, url=url, json_body=body, vehicle=vehicle
        )
        response_body = response.json()


    def check_last_action_status(self, token: Token, vehicle: Vehicle, action_id: str):
        url = self.API_URL + "cmm/gts"
        body = {"xid": action_id}
        response = self.post_request_with_logging_and_active_session(
            token=token, url=url, json_body=body, vehicle=vehicle.id
        )
        response_json = response.json()
        last_action_completed = all(
            v == 0 for v in response_json["payload"].values()
        )
        return last_action_completed

    def lock_action(self, token: Token, vehicle: Vehicle, action) -> str:
        _LOGGER.debug(f"{DOMAIN} - Action for lock is: {action}")
        if action == VEHICLE_LOCK_ACTION.LOCK:
            url = self.API_URL + "rems/door/lock"
            _LOGGER.debug(f"{DOMAIN} - Calling Lock")
        elif action == VEHICLE_LOCK_ACTION.UNLOCK:
            url = self.API_URL + "rems/door/unlock"
            _LOGGER.debug(f"{DOMAIN} - Calling unlock")

        response = self.get_request_with_logging_and_active_session(
            token=token, url=url, vehicle=vehicle
        )

        return response.headers["Xid"]

    def start_climate(
        self,
        token: Token,
        vehicle: Vehicle,
        options: ClimateRequestOptions
    ) -> str:
        url = self.API_URL + "rems/start"
        if options.set_temp < 62:
            options.set_temp = "LOW"
        elif options.set_temp > 82:
            options.set_temp = "HIGH"
        body = {
            "remoteClimate": {
                "airCtrl": options.climate,
                "airTemp": {
                    "unit": 1,
                    "value": str(options.set_temp),
                },
                "defrost": options.defrost,
                "heatingAccessory": {
                    "rearWindow": int(options.heating),
                    "sideMirror": int(options.heating),
                    "steeringWheel": int(options.heating),
                },
                "ignitionOnDuration": {
                    "unit": 4,
                    "value": options.duration,
                },
            }
        }
        _LOGGER.debug(f"{DOMAIN} - Planned start_climate payload: {body}")
        response = self.post_request_with_logging_and_active_session(
            token=token, url=url, json_body=body, vehicle=vehicle
        )
        return response.headers["Xid"]

    def stop_climate(self, token: Token, vehicle: Vehicle)-> str:
        url = self.API_URL + "rems/stop"
        response = self.get_request_with_logging_and_active_session(
            token=token, url=url
        )
        return response.headers["Xid"]

    def start_charge(self, token: Token, vehicle: Vehicle)-> str:
        url = self.API_URL + "evc/charge"
        body = {"chargeRatio": 100}
        response = self.post_request_with_logging_and_active_session(
            token=token, url=url, json_body=body
        )
        return response.headers["Xid"]

    def stop_charge(self, token: Token, vehicle: Vehicle)-> str:
        url = self.API_URL + "evc/cancel"
        response = self.get_request_with_logging_and_active_session(
            token=token, url=url, vehicle=vehicle
        )
        return response.headers["Xid"]

    def set_charge_limits(self, token: Token, vehicle: Vehicle, ac: int, dc: int)-> str:
        url = self.API_URL + "evc/sts"
        body = {
            "targetSOClist": [
                {
                    "plugType": 0,
                    "targetSOClevel": dc,
                },
                {
                    "plugType": 1,
                    "targetSOClevel": ac,
                },
            ]
        }
        response = self.post_request_with_logging_and_active_session(
            token=token, url=url, json_body=body, vechile=vehicle
        )
        return response.headers["Xid"]
