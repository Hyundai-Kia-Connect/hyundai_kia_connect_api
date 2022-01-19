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
)
from .Token import Token
from .utils import (
    get_child_value,
    get_hex_temp_into_index,
    get_index_into_hex_temp,
)
from .Vehicle import Vehicle

_LOGGER = logging.getLogger(__name__)


class KiaUvoApiCA(ApiImpl):
    temperature_range = [x * 0.5 for x in range(32, 64)]

    def __init__(self, region: int, brand: int) -> None:

        self.last_action_tracked = True
        self.last_action_xid = None
        self.last_action_completed = False
        self.last_action_pin_auth = None

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
            valid_until=valid_until,
        )

    def get_vehicles(self, token: Token) -> list[Vehicle]:
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
                year=entry["modelYear"],
                VIN=entry["vin"],
            )
            result.append(vehicle)
        return result

    def update_vehicle_with_cached_state(self, token: Token, vehicle: Vehicle) -> None:
        state = self._get_cached_vehicle_state(token, vehicle)
        vehicle.last_updated_at = self.get_last_updated_at(
            get_child_value(state, "status.lastStatusDate")
        )
        vehicle.total_driving_distance = (
            get_child_value(
                state,
                "status.evStatus.drvDistance.0.rangeByFuel.totalAvailableRange.value",
            ),
            DISTANCE_UNITS[
                get_child_value(
                    state,
                    "status.evStatus.drvDistance.0.rangeByFuel.totalAvailableRange.unit",
                )
            ],
        )
        vehicle.odometer = (
            get_child_value(state, "service.currentOdometer"),
            DISTANCE_UNITS[get_child_value(state, "service.currentOdometerUnit")],
        )
        vehicle.next_service_distance = (
            get_child_value(state, "service.imatServiceOdometer"),
            DISTANCE_UNITS[get_child_value(state, "service.imatServiceOdometerUnit")],
        )
        vehicle.last_service_distance = (
            get_child_value(state, "service.msopServiceOdometer"),
            DISTANCE_UNITS[get_child_value(state, "service.msopServiceOdometerUnit")],
        )
        vehicle.car_battery_percentage = get_child_value(state, "status.battery.batSoc")
        vehicle.engine_is_running = get_child_value(state, "status.engine")
        vehicle.air_temperature = (
            get_child_value(state, "status.airTemp.value"),
            "c",
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
        vehicle.front_left_seat_heater_is_on = get_child_value(
            state, "status.seatHeaterVentState.flSeatHeatState"
        )
        vehicle.front_right_seat_heater_is_on = get_child_value(
            state, "status.seatHeaterVentState.frSeatHeatState"
        )
        vehicle.rear_left_seat_heater_is_on = get_child_value(
            state, "status.seatHeaterVentState.rlSeatHeatState"
        )
        vehicle.rear_right_seat_heater_is_on = get_child_value(
            state, "status.seatHeaterVentState.rrSeatHeatState"
        )
        vehicle.is_locked = not get_child_value(state, "status.doorLock")
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
        vehicle.ev_battery_percentage = get_child_value(
            state, "status.evStatus.batteryStatus"
        )
        vehicle.ev_battery_is_charging = get_child_value(
            state, "status.evStatus.batteryCharge"
        )
        vehicle.ev_battery_is_plugged_in = get_child_value(
            state, "status.evStatus.batteryPlugin"
        )
        vehicle.ev_driving_distance = (
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
        vehicle.fuel_driving_distance = (
            get_child_value(
                state,
                "status.dte.value",
            ),
            DISTANCE_UNITS[get_child_value(state, "status.dte.unit")],
        )
        vehicle.fuel_level_is_low = get_child_value(state, "status.lowFuelLight")
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
        # Vehicle Status Call
        url = self.API_URL + "lstvhclsts"
        headers = self.API_HEADERS
        headers["accessToken"] = token.access_token
        headers["vehicleId"] = vehicle.id

        response = requests.post(url, headers=headers)
        response = response.json()
        _LOGGER.debug(f"{DOMAIN} - get_cached_vehicle_status response {response}")
        response = response["result"]["status"]

        # Converts temp to usable number. Currently only support celsius. Future to do is check unit in case the care itself is set to F.
        tempIndex = get_hex_temp_into_index(get_child_value(response, "airTemp.value"))
        if get_child_value(response, "airTemp.unit") == 0:
            response["airTemp"]["value"] = self.temperature_range[tempIndex]

        status = {}
        status["status"] = response

        # Service Status Call
        status["service"] = self._get_next_service(token, vehicle)
        if vehicle.odometer:
            if vehicle.odometer < get_child_value(status, "service.currentOdometer"):
                status["vehicleLocation"] = self.get_location(token, vehicle)
            else:
                status["vehicleLocation"] = None
        else:
            status["vehicleLocation"] = self.get_location(token, vehicle)

        return status

    def _get_next_service(self, token: Token, vehicle: Vehicle) -> None:
        headers = self.API_HEADERS
        headers["accessToken"] = token.access_token
        headers["vehicleId"] = vehicle.id
        url = self.API_URL + "nxtsvc"
        response = requests.post(url, headers=headers)
        response = response.json()
        _LOGGER.debug(f"{DOMAIN} - Get Service status data {response}")
        response = response["result"]["maintenanceInfo"]
        return response

    def get_location(self, token: Token, vehicle: Vehicle) -> None:
        url = self.API_URL + "fndmcr"
        headers = self.API_HEADERS
        headers["accessToken"] = token.access_token
        headers["vehicleId"] = vehicle.id
        try:
            headers["pAuth"] = self._get_pin_token(token, vehicle)

            response = requests.post(
                url, headers=headers, data=json.dumps({"pin": token.pin})
            )
            response = response.json()
            _LOGGER.debug(f"{DOMAIN} - Get Vehicle Location {response}")
            if response["responseHeader"]["responseCode"] != 0:
                raise Exception("No Location Located")
            return response["result"]
        except:
            _LOGGER.warning(f"{DOMAIN} - Get vehicle location failed")
            return None

    def _get_pin_token(self, token: Token, vehicle: Vehicle) -> None:
        url = self.API_URL + "vrfypin"
        headers = self.API_HEADERS
        headers["accessToken"] = token.access_token
        headers["vehicleId"] = vehicle.id

        response = requests.post(
            url, headers=headers, data=json.dumps({"pin": token.pin})
        )
        _LOGGER.debug(f"{DOMAIN} - Received Pin validation response {response}")
        result = response.json()["result"]

        return result["pAuth"]

    def force_refresh_vehicle_state(self, token: Token, vehicle: Vehicle) -> None:
        url = self.API_URL + "rltmvhclsts"
        headers = self.API_HEADERS
        headers["accessToken"] = token.access_token
        headers["vehicleId"] = vehicle.id

        response = requests.post(url, headers=headers)
        response = response.json()
        _LOGGER.debug(f"{DOMAIN} - Received forced vehicle data {response}")

    def lock_action(self, token: Token, action, vehicle: Vehicle) -> None:
        _LOGGER.debug(f"{DOMAIN} - Action for lock is: {action}")
        if action == "close":
            url = self.API_URL + "drlck"
            _LOGGER.debug(f"{DOMAIN} - Calling Lock")
        else:
            url = self.API_URL + "drulck"
            _LOGGER.debug(f"{DOMAIN} - Calling unlock")
        headers = self.API_HEADERS
        headers["accessToken"] = token.access_token
        headers["vehicleId"] = vehicle.id
        headers["pAuth"] = self._get_pin_token(token, vehicle)

        response = requests.post(
            url, headers=headers, data=json.dumps({"pin": token.pin})
        )
        response_headers = response.headers
        response = response.json()
        self.last_action_xid = response_headers["transactionId"]
        self.last_action_pin_auth = headers["pAuth"]

        _LOGGER.debug(f"{DOMAIN} - Received lock_action response")

    def start_climate(
        self, token: Token, vehicle: Vehicle, options: ClimateRequestOptions
    ) -> None:
        url = self.API_URL + "rmtstrt"
        headers = self.API_HEADERS
        headers["accessToken"] = token.access_token
        headers["vehicleId"] = vehicle.id
        headers["pAuth"] = self._get_pin_token(token)

        hex_set_temp = get_index_into_hex_temp(
            self.temperature_range.index(options.set_temp)
        )

        payload = {
            "setting": {
                "airCtrl": int(options.climate),
                "defrost": options.defrost,
                "heating1": int(options.heating),
                "igniOnDuration": options.duration,
                "ims": 0,
                "airTemp": {"value": hex_set_temp, "unit": 0, "hvacTempType": 0},
            },
            "pin": token.pin,
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
        self, token: Token, vehicle: Vehicle, options: ClimateRequestOptions
    ) -> None:
        # TODO: get rid of this function as we can access all vehicle information from `start_climate` function
        url = self.API_URL + "evc/rfon"
        headers = self.API_HEADERS
        headers["accessToken"] = token.access_token
        headers["vehicleId"] = vehicle.id
        headers["pAuth"] = self._get_pin_token(token, vehicle.id)

        hex_set_temp = get_index_into_hex_temp(self.temperature_range.index(set_temp))

        payload = {
            "hvacInfo": {
                "airCtrl": int(options.climate),
                "defrost": options.defrost,
                "heating1": int(options.heating),
                "airTemp": {
                    "value": hex_set_temp,
                    "unit": 0,
                    "hvacTempType": 1,
                },
            },
            "pin": token.pin,
        }

        data = json.dumps(payload)
        # _LOGGER.debug(f"{DOMAIN} - Planned start_climate_ev payload {payload}")

        response = requests.post(url, headers=headers, data=json.dumps(payload))
        response_headers = response.headers
        response = response.json()

        self.last_action_xid = response_headers["transactionId"]
        self.last_action_pin_auth = headers["pAuth"]
        _LOGGER.debug(f"{DOMAIN} - Received start_climate_ev response {response}")

    def stop_climate(self, token: Token, vehicle: Vehicle) -> None:
        url = self.API_URL + "rmtstp"
        headers = self.API_HEADERS
        headers["accessToken"] = token.access_token
        headers["vehicleId"] = vehicle.id
        headers["pAuth"] = self.get_pin_token(token, vehicle.id)

        response = requests.post(
            url, headers=headers, data=json.dumps({"pin": token.pin})
        )
        response_headers = response.headers
        response = response.json()

        self.last_action_xid = response_headers["transactionId"]
        self.last_action_pin_auth = headers["pAuth"]

        _LOGGER.debug(f"{DOMAIN} - Received stop_climate response")

    def stop_climate_ev(self, token: Token, vehicle: Vehicle) -> None:
        url = self.API_URL + "evc/rfoff"
        headers = self.API_HEADERS
        headers["accessToken"] = token.access_token
        headers["vehicleId"] = vehicle.id
        headers["pAuth"] = self._get_pin_token(token, vehicle.id)

        response = requests.post(
            url, headers=headers, data=json.dumps({"pin": token.pin})
        )
        response_headers = response.headers
        response = response.json()

        self.last_action_xid = response_headers["transactionId"]
        self.last_action_pin_auth = headers["pAuth"]

        _LOGGER.debug(f"{DOMAIN} - Received stop_climate response")

    def check_last_action_status(self, token: Token, vehicle: Vehicle) -> None:
        url = self.API_URL + "rmtsts"
        headers = self.API_HEADERS
        headers["accessToken"] = token.access_token
        headers["vehicleId"] = vehicle.id
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

    def start_charge(self, token: Token, vehicle: Vehicle) -> None:
        url = self.API_URL + "evc/rcstrt"
        headers = self.API_HEADERS
        headers["accessToken"] = token.access_token
        headers["vehicleId"] = vehicle.id
        headers["pAuth"] = self._get_pin_token(token, vehicle.id)

        response = requests.post(
            url, headers=headers, data=json.dumps({"pin": token.pin})
        )
        response_headers = response.headers
        response = response.json()

        _LOGGER.debug(f"{DOMAIN} - Received start_charge response {response}")

    def stop_charge(self, token: Token, vehicle: Vehicle) -> None:
        url = self.API_URL + "evc/rcstp"
        headers = self.API_HEADERS
        headers["accessToken"] = token.access_token
        headers["vehicleId"] = vehicle.id
        headers["pAuth"] = self.get_pin_token(token, vehicle.id)

        response = requests.post(
            url, headers=headers, data=json.dumps({"pin": token.pin})
        )
        response_headers = response.headers
        response = response.json()

        _LOGGER.debug(f"{DOMAIN} - Received start_charge response {response}")
