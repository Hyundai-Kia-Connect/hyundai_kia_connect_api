import logging
import random
import secrets
import string
import time
from datetime import datetime, timedelta

import pytz
import requests
from requests import RequestException, Response

from .const import DOMAIN
from .ApiImpl import ApiImpl
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
            _LOGGER.debug(f"got invalid session, attempting to repair and resend")
            self = args[0]
            token = kwargs["token"]
            new_token = self.login()
            _LOGGER.debug(
                f"old token:{token.access_token}, new token:{new_token.access_token}"
            )
            token.access_token = new_token.access_token
            token.vehicle_regid = new_token.vehicle_regid
            token.valid_until = new_token.valid_until
            json_body = kwargs.get("json_body", None)
            if json_body is not None and json_body.get("vinKey", None):
                json_body["vinKey"] = [token.vehicle_regid]
            response = func(*args, **kwargs)
            return response

    return request_with_active_session_wrapper


def request_with_logging(func):
    def request_with_logging_wrapper(*args, **kwargs):
        url = kwargs["url"]
        json_body = kwargs.get("json_body")
        if json_body is not None:
            _LOGGER.debug(f"sending {url} request with {json_body}")
        else:
            _LOGGER.debug(f"sending {url} request")
        response = func(*args, **kwargs)
        _LOGGER.debug(f"got response {response.text}")
        response_json = response.json()
        if response_json["status"]["statusCode"] == 0:
            return response
        if (
            response_json["status"]["statusCode"] == 1
            and response_json["status"]["errorType"] == 1
            and response_json["status"]["errorCode"] == 1003
        ):
            _LOGGER.debug(f"error: session invalid")
            raise AuthError
        _LOGGER.error(f"error: unknown error response {response.text}")
        raise RequestException

    return request_with_logging_wrapper


class KiaUvoAPIUSA(ApiImpl):
    def __init__(
        self,
        region: int,
        brand: int,
    ) -> None:
        self.last_action_tracked = True
        self.last_action_xid = None
        self.last_action_completed = False
        self.temperature_range = range(62, 82)

        self.supports_soc_range = False

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

    def authed_api_headers(self, token: Token):
        headers = self.api_headers()
        headers["sid"] = token.access_token
        headers["vinkey"] = token.vehicle_regid
        return headers

    @request_with_active_session
    @request_with_logging
    def post_request_with_logging_and_active_session(
        self, token: Token, url: str, json_body: dict
    ) -> Response:
        headers = self.authed_api_headers(token)
        return requests.post(url, json=json_body, headers=headers)

    @request_with_active_session
    @request_with_logging
    def get_request_with_logging_and_active_session(
        self, token: Token, url: str
    ) -> Response:
        headers = self.authed_api_headers(token)
        return requests.get(url, headers=headers)

    def login(self, username: str, password: str) -> Token:

        # Sign In with Email and Password and Get Authorization Code

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
        valid_until = (datetime.now() + timedelta(hours=1)).strftime(DATE_FORMAT)
        return Token(
            username=username,
            password=password,
            access_token=session_id,
            valid_until=valid_until,
        )

    def get_vehicles(self, token: Token) -> list[Vehicle]:
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
                registration_date=None,
            )
            result.append(vehicle)
        return result

    def update_vehicle_with_cached_state(self, token: Token, vehicle: Vehicle) -> None:
        state = self._get_cached_vehicle_state(token, vehicle.id)
        vehicle.last_updated_at = self.get_last_updated_at(
            get_child_value(state, "vehicleStatus.lastStatusDate")
        )
        vehicle.total_driving_distance = (
            get_child_value(
                state,
                "vehicleStatus.evStatus.drvDistance.0.rangeByFuel.totalAvailableRange.value",
            ),
            "km",
        )
        vehicle.odometer = (
            get_child_value(state, "service.currentOdometer"),
            "km",
        )
        vehicle.next_service_distance = (
            get_child_value(state, "service.imatServiceOdometer"),
            "km",
        )
        vehicle.last_service_distance = (
            get_child_value(state, "service.msopServiceOdometer"),
            "km",
        )
        vehicle.car_battery_percentage = get_child_value(
            state, "vehicleStatus.battery.batSoc"
        )
        vehicle.engine_is_running = get_child_value(state, "vehicleStatus.engine")
        vehicle.air_temperature = (
            get_child_value(state, "vehicleStatus.airTemp.value"),
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
        vehicle.is_locked = not get_child_value(state, "vehicleStatus.doorLock")
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

    def _get_cached_vehicle_state(self, token: Token, vehicle_id: str) -> dict:
        url = self.API_URL + "cmm/gvi"

        body = {
            "vehicleConfigReq": {
                "airTempRange": "0",
                "maintenance": "0",
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
            "vinKey": [token.vehicle_regid],
        }
        response = self.post_request_with_logging_and_active_session(
            token=token, url=url, json_body=body
        )

        response_body = response.json()
        vehicle_status = response_body["payload"]["vehicleInfoList"][0][
            "lastVehicleInfo"
        ]["vehicleStatusRpt"]["vehicleStatus"]
        vehicle_data = {
            "vehicleStatus": vehicle_status,
            "odometer": {
                "value": float(
                    response_body["payload"]["vehicleInfoList"][0]["vehicleConfig"][
                        "vehicleDetail"
                    ]["vehicle"]["mileage"]
                ),
                "unit": 3,
            },
            "vehicleLocation": response_body["payload"]["vehicleInfoList"][0][
                "lastVehicleInfo"
            ]["location"],
        }

        vehicle_status["time"] = vehicle_status["syncDate"]["utc"]

        if vehicle_status["batteryStatus"].get("stateOfCharge"):
            vehicle_status["battery"] = {
                "batSoc": vehicle_status["batteryStatus"]["stateOfCharge"],
            }

        if vehicle_status.get("evStatus"):
            vehicle_status["evStatus"]["remainTime2"] = {
                "atc": vehicle_status["evStatus"]["remainChargeTime"][0]["timeInterval"]
            }

        vehicle_status["doorOpen"] = vehicle_status["doorStatus"]
        vehicle_status["trunkOpen"] = vehicle_status["doorStatus"]["trunk"]
        vehicle_status["hoodOpen"] = vehicle_status["doorStatus"]["hood"]

        if vehicle_status.get("tirePressure"):
            vehicle_status["tirePressureLamp"] = {
                "tirePressureLampAll": vehicle_status["tirePressure"]["all"]
            }

        climate_data = vehicle_status["climate"]
        vehicle_status["airCtrlOn"] = climate_data["airCtrl"]
        vehicle_status["defrost"] = climate_data["defrost"]
        vehicle_status["sideBackWindowHeat"] = climate_data["heatingAccessory"][
            "rearWindow"
        ]
        vehicle_status["sideMirrorHeat"] = climate_data["heatingAccessory"][
            "sideMirror"
        ]
        vehicle_status["steerWheelHeat"] = climate_data["heatingAccessory"][
            "steeringWheel"
        ]

        vehicle_status["airTemp"] = climate_data["airTemp"]

        return vehicle_data

    def get_location(self, token: Token, vehicle_id: str) -> None:
        pass

    def _get_pin_token(self, token: Token, vehicle_id: str) -> None:
        pass

    def update_vehicle_status(self, token: Token):
        url = self.API_URL + "rems/rvs"
        body = {
            "requestType": 0  # value of 1 would return cached results instead of forcing update
        }
        self.post_request_with_logging_and_active_session(
            token=token, url=url, json_body=body
        )

    def check_last_action_status(self, token: Token):
        url = self.API_URL + "cmm/gts"
        body = {"xid": self.last_action_xid}
        response = self.post_request_with_logging_and_active_session(
            token=token, url=url, json_body=body
        )
        response_json = response.json()
        self.last_action_completed = all(
            v == 0 for v in response_json["payload"].values()
        )
        return self.last_action_completed

    def lock_action(self, token: Token, action, vehicle_id: str) -> None:
        _LOGGER.debug(f"Action for lock is: {action}")
        if action == "close":
            url = self.API_URL + "rems/door/lock"
            _LOGGER.debug(f"Calling Lock")
        else:
            url = self.API_URL + "rems/door/unlock"
            _LOGGER.debug(f"Calling unlock")

        response = self.get_request_with_logging_and_active_session(
            token=token, url=url
        )

        self.last_action_xid = response.headers["Xid"]

    def start_climate(
        self, token: Token, vehicle_id: str, set_temp, duration, defrost, climate, heating
    ) -> None:
        url = self.API_URL + "rems/start"
        body = {
            "remoteClimate": {
                "airCtrl": climate,
                "airTemp": {
                    "unit": 1,
                    "value": str(set_temp),
                },
                "defrost": defrost,
                "heatingAccessory": {
                    "rearWindow": int(heating),
                    "sideMirror": int(heating),
                    "steeringWheel": int(heating),
                },
                "ignitionOnDuration": {
                    "unit": 4,
                    "value": duration,
                },
            }
        }
        response = self.post_request_with_logging_and_active_session(
            token=token, url=url, json_body=body
        )
        self.last_action_xid = response.headers["Xid"]

    def stop_climate(self, token: Token, vehicle_id: str)-> None:
        url = self.API_URL + "rems/stop"
        response = self.get_request_with_logging_and_active_session(
            token=token, url=url
        )
        self.last_action_xid = response.headers["Xid"]

    def start_charge(self, token: Token, vehicle_id: str)-> None:
        url = self.API_URL + "evc/charge"
        body = {"chargeRatio": 100}
        response = self.post_request_with_logging_and_active_session(
            token=token, url=url, json_body=body
        )
        self.last_action_xid = response.headers["Xid"]

    def stop_charge(self, token: Token, vehicle_id: str)-> None:
        url = self.API_URL + "evc/cancel"
        response = self.get_request_with_logging_and_active_session(
            token=token, url=url
        )
        self.last_action_xid = response.headers["Xid"]

    def set_charge_limits(self, token: Token, vehicle_id: str, ac_limit: int, dc_limit: int)-> None:
        url = self.API_URL + "evc/sts"
        body = {
            "targetSOClist": [
                {
                    "plugType": 0,
                    "targetSOClevel": dc_limit,
                },
                {
                    "plugType": 1,
                    "targetSOClevel": ac_limit,
                },
            ]
        }
        response = self.post_request_with_logging_and_active_session(
            token=token, url=url, json_body=body
        )
        self.last_action_xid = response.headers["Xid"]
