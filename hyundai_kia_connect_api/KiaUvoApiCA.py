import json
import logging
import datetime as dt

import requests
import pytz

from .ApiImpl import ApiImpl
from .const import BRAND_HYUNDAI, BRAND_KIA, BRANDS, DOMAIN
from .Token import Token
from .utils import get_child_value, get_hex_temp_into_index
from .Vehicle import Vehicle

_LOGGER = logging.getLogger(__name__)


class KiaUvoApiCA(ApiImpl):
    def __init__(self, region: int, brand: int) -> None:

        self.last_action_tracked = True
        self.last_action_xid = None
        self.last_action_completed = False
        self.last_action_pin_auth = None
        self.temperature_range = [x * 0.5 for x in range(32, 64)]

        if BRANDS[brand] == BRAND_KIA:
            self.BASE_URL: str = "kiaconnect.ca"
        elif BRANDS[brand] == BRAND_HYUNDAI:
            self.BASE_URL: str = "www.mybluelink.ca"
        self.old_vehicle_status = {}
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

        # Sign In with Email and Password and Get Authorization Code

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
            device_id=None,
            stamp=None,
            valid_until=valid_until,
        )

        return token

    def get_vehicles(self, token: Token):
        url = self.API_URL + "vhcllst"
        headers = self.API_HEADERS
        headers["accessToken"] = token.access_token
        response = requests.post(url, headers=headers)
        _LOGGER.debug(f"{DOMAIN} - Get Vehicles Response {response.text}")
        response = response.json()
        result = []
        for entry in response["result"]["vehicles"]:
            vehicle: Vehicle = Vehicle(
                id=entry["vehicleId"],
                name=entry["nickName"],
                model=entry["modelName"],
                registration_date=None,
            )
            result.append(vehicle)
        return result

    def update_vehicle_with_cached_state(self, token: Token, vehicle: Vehicle) -> None:
        state = self._get_cached_vehicle_state(token, vehicle.id)
        vehicle.last_updated_at = self.get_last_updated_at(
            get_child_value(state, "vehicleStatus.time")
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
        # Vehicle Status Call
        url = self.API_URL + "lstvhclsts"
        headers = self.API_HEADERS
        headers["accessToken"] = token.access_token
        headers["vehicleId"] = vehicle_id

        response = requests.post(url, headers=headers)
        response = response.json()
        _LOGGER.debug(f"{DOMAIN} - get_cached_vehicle_status response {response}")
        response = response["result"]["status"]

        vehicle_status = {}
        vehicle_status["vehicleStatus"] = response

        # Coverts temp to usable number.  Currently only support celsius. Future to do is check unit in case the care itself is set to F.
        tempIndex = get_hex_temp_into_index(
            vehicle_status["vehicleStatus"]["airTemp"]["value"]
        )
        if (vehicle_status["vehicleStatus"]["airTemp"]["unit"]) == 0:
            vehicle_status["vehicleStatus"]["airTemp"][
                "value"
            ] = self.temperature_range[tempIndex]

        vehicle_status["vehicleStatus"]["time"] = response["lastStatusDate"]

        # Service Status Call
        response = self.get_next_service(token, vehicle_id)

        vehicle_status["odometer"] = {}
        vehicle_status["odometer"]["unit"] = response["currentOdometerUnit"]
        vehicle_status["odometer"]["value"] = response["currentOdometer"]
        vehicle_status["nextService"] = {}
        vehicle_status["nextService"]["unit"] = response["imatServiceOdometerUnit"]
        vehicle_status["nextService"]["value"] = response["imatServiceOdometer"]

        # Handles cars that have never had service
        if response.get("msopServiceOdometer"):
            vehicle_status["lastService"] = {}
            vehicle_status["lastService"]["unit"] = response["msopServiceOdometerUnit"]
            vehicle_status["lastService"]["value"] = response["msopServiceOdometer"]

        if not self.old_vehicle_status == {}:
            if (
                vehicle_status["odometer"]["value"]
                > self.old_vehicle_status["odometer"]["value"]
            ):
                vehicle_status["vehicleLocation"] = self.get_location(token)
            else:
                vehicle_status["vehicleLocation"] = self.old_vehicle_status[
                    "vehicleLocation"
                ]
        else:
            vehicle_status["vehicleLocation"] = self.get_location(token, vehicle_id)

        self.old_vehicle_status = vehicle_status
        return vehicle_status

    def get_next_service(self, token: Token, vehicle_id: str) -> None:
        headers = self.API_HEADERS
        headers["accessToken"] = token.access_token
        headers["vehicleId"] = vehicle_id
        url = self.API_URL + "nxtsvc"
        response = requests.post(url, headers=headers)
        response = response.json()
        _LOGGER.debug(f"{DOMAIN} - Get Service status data {response}")
        response = response["result"]["maintenanceInfo"]
        return response

    def get_location(self, token: Token, vehicle_id: str) -> None:
        url = self.API_URL + "fndmcr"
        headers = self.API_HEADERS
        headers["accessToken"] = token.access_token
        headers["vehicleId"] = vehicle_id
        try:
            headers["pAuth"] = self.get_pin_token(token)

            response = requests.post(
                url, headers=headers, data=json.dumps({"pin": self.pin})
            )
            response = response.json()
            _LOGGER.debug(f"{DOMAIN} - Get Vehicle Location {response}")
            if response["responseHeader"]["responseCode"] != 0:
                raise Exception("No Location Located")

        except:
            _LOGGER.warning(f"{DOMAIN} - Get vehicle location failed")
            response = None
            return response
        else:
            return response["result"]

    def get_pin_token(self, token: Token, vehicle_id: str) -> None:
        url = self.API_URL + "vrfypin"
        headers = self.API_HEADERS
        headers["accessToken"] = token.access_token
        headers["vehicleId"] = vehicle_id

        response = requests.post(
            url, headers=headers, data=json.dumps({"pin": self.pin})
        )
        _LOGGER.debug(f"{DOMAIN} - Received Pin validation response {response}")
        result = response.json()["result"]

        return result["pAuth"]

    def update_vehicle_status(self, token: Token, vehicle_id: str) -> None:
        url = self.API_URL + "rltmvhclsts"
        headers = self.API_HEADERS
        headers["accessToken"] = token.access_token
        headers["vehicleId"] = vehicle_id

        response = requests.post(url, headers=headers)
        response = response.json()
        _LOGGER.debug(f"{DOMAIN} - Received forced vehicle data {response}")

    def lock_action(self, token: Token, action, vehicle_id: str) -> None:
        _LOGGER.debug(f"{DOMAIN} - Action for lock is: {action}")
        if action == "close":
            url = self.API_URL + "drlck"
            _LOGGER.debug(f"{DOMAIN} - Calling Lock")
        else:
            url = self.API_URL + "drulck"
            _LOGGER.debug(f"{DOMAIN} - Calling unlock")
        headers = self.API_HEADERS
        headers["accessToken"] = token.access_token
        headers["vehicleId"] = vehicle_id
        headers["pAuth"] = self.get_pin_token(token)

        response = requests.post(
            url, headers=headers, data=json.dumps({"pin": self.pin})
        )
        response_headers = response.headers
        response = response.json()
        self.last_action_xid = response_headers["transactionId"]
        self.last_action_pin_auth = headers["pAuth"]

        _LOGGER.debug(f"{DOMAIN} - Received lock_action response")

    def start_climate(
        self, token: Token, vehicle_id, set_temp, duration, defrost, climate, heating
    ) -> None:
        url = self.API_URL + "rmtstrt"
        headers = self.API_HEADERS
        headers["accessToken"] = token.access_token
        headers["vehicleId"] = vehicle_id
        headers["pAuth"] = self.get_pin_token(token)

        set_temp = self.get_temperature_range_by_region().index(set_temp)
        set_temp = hex(set_temp).split("x")
        set_temp = set_temp[1] + "H"
        set_temp = set_temp.zfill(3).upper()

        payload = {
            "setting": {
                "airCtrl": int(climate),
                "defrost": defrost,
                "heating1": int(heating),
                "igniOnDuration": duration,
                "ims": 0,
                "airTemp": {"value": set_temp, "unit": 0, "hvacTempType": 0},
            },
            "pin": self.pin,
        }
        data = json.dumps(payload)
        # _LOGGER.debug(f"{DOMAIN} - Planned start_climate payload {payload}")

        response = requests.post(url, headers=headers, data=json.dumps(payload))
        response_headers = response.headers
        response = response.json()

        self.last_action_xid = response_headers["transactionId"]
        self.last_action_pin_auth = headers["pAuth"]

        _LOGGER.debug(f"{DOMAIN} - Received start_climate response {response}")

    def start_climate_ev(
        self, token: Token, vehicle_id, set_temp, duration, defrost, climate, heating
    ) -> None:
        url = self.API_URL + "evc/rfon"
        headers = self.API_HEADERS
        headers["accessToken"] = token.access_token
        headers["vehicleId"] = vehicle_id
        headers["pAuth"] = self.get_pin_token(token)

        set_temp = self.get_temperature_range_by_region().index(set_temp)
        set_temp = hex(set_temp).split("x")
        set_temp = set_temp[1] + "H"
        set_temp = set_temp.zfill(3).upper()

        payload = {
            "hvacInfo": {
                "airCtrl": int(climate),
                "defrost": defrost,
                "heating1": int(heating),
                "airTemp": {
                    "value": set_temp,
                    "unit": 0,
                    "hvacTempType": 1,
                },
            },
            "pin": self.pin,
        }

        data = json.dumps(payload)
        # _LOGGER.debug(f"{DOMAIN} - Planned start_climate_ev payload {payload}")

        response = requests.post(url, headers=headers, data=json.dumps(payload))
        response_headers = response.headers
        response = response.json()

        self.last_action_xid = response_headers["transactionId"]
        self.last_action_pin_auth = headers["pAuth"]
        _LOGGER.debug(f"{DOMAIN} - Received start_climate_ev response {response}")

    def stop_climate(self, token: Token, vehicle_id: str) -> None:
        url = self.API_URL + "rmtstp"
        headers = self.API_HEADERS
        headers["accessToken"] = token.access_token
        headers["vehicleId"] = vehicle_id
        headers["pAuth"] = self.get_pin_token(token)

        response = requests.post(
            url, headers=headers, data=json.dumps({"pin": self.pin})
        )
        response_headers = response.headers
        response = response.json()

        self.last_action_xid = response_headers["transactionId"]
        self.last_action_pin_auth = headers["pAuth"]

        _LOGGER.debug(f"{DOMAIN} - Received stop_climate response")

    def stop_climate_ev(self, token: Token, vehicle_id: str) -> None:
        url = self.API_URL + "evc/rfoff"
        headers = self.API_HEADERS
        headers["accessToken"] = token.access_token
        headers["vehicleId"] = vehicle_id
        headers["pAuth"] = self.get_pin_token(token)

        response = requests.post(
            url, headers=headers, data=json.dumps({"pin": self.pin})
        )
        response_headers = response.headers
        response = response.json()

        self.last_action_xid = response_headers["transactionId"]
        self.last_action_pin_auth = headers["pAuth"]

        _LOGGER.debug(f"{DOMAIN} - Received stop_climate response")

    def check_last_action_status(self, token: Token, vehicle_id: str) -> None:
        url = self.API_URL + "rmtsts"
        headers = self.API_HEADERS
        headers["accessToken"] = token.access_token
        headers["vehicleId"] = vehicle_id
        headers["transactionId"] = self.last_action_xid
        headers["pAuth"] = self.last_action_pin_auth
        response = requests.post(url, headers=headers)
        response = response.json()

        self.last_action_completed = (
            response["result"]["transaction"]["apiStatusCode"] != "null"
        )
        if self.last_action_completed:
            action_status = response["result"]["transaction"]["apiStatusCode"]
            _LOGGER.debug(f"{DOMAIN} - Last action_status: {action_status}")
        return self.last_action_completed

    def start_charge(self, token: Token, vehicle_id: str) -> None:
        url = self.API_URL + "evc/rcstrt"
        headers = self.API_HEADERS
        headers["accessToken"] = token.access_token
        headers["vehicleId"] = vehicle_id
        headers["pAuth"] = self.get_pin_token(token)

        response = requests.post(
            url, headers=headers, data=json.dumps({"pin": self.pin})
        )
        response_headers = response.headers
        response = response.json()

        _LOGGER.debug(f"{DOMAIN} - Received start_charge response {response}")

    def stop_charge(self, token: Token, vehicle_id: str) -> None:
        url = self.API_URL + "evc/rcstp"
        headers = self.API_HEADERS
        headers["accessToken"] = token.access_token
        headers["vehicleId"] = token.vehicle_id
        headers["pAuth"] = self.get_pin_token(token)

        response = requests.post(
            url, headers=headers, data=json.dumps({"pin": self.pin})
        )
        response_headers = response.headers
        response = response.json()

        _LOGGER.debug(f"{DOMAIN} - Received start_charge response {response}")
