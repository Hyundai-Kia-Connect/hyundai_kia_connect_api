import datetime as dt
import logging
import random
import re
import uuid
from urllib.parse import parse_qs, urlparse

import pytz
import requests
from bs4 import BeautifulSoup
from dateutil import tz, parser

from .ApiImpl import (
    ApiImpl,
    ClimateRequestOptions,
)
from .const import (
    BRAND_HYUNDAI,
    BRAND_KIA,
    BRANDS,
    DOMAIN,
    DISTANCE_UNITS,
    TEMPERATURE_UNITS,
    SEAT_STATUS,
    VEHICLE_LOCK_ACTION,
    CHARGE_PORT_ACTION,
    ENGINE_TYPES,
)

from .exceptions import *
from .Token import Token
from .utils import get_child_value, get_index_into_hex_temp, get_hex_temp_into_index
from .Vehicle import Vehicle, DailyDrivingStats

_LOGGER = logging.getLogger(__name__)

INVALID_STAMP_RETRY_COUNT = 10
USER_AGENT_OK_HTTP: str = "okhttp/3.12.0"
USER_AGENT_MOZILLA: str = "Mozilla/5.0 (Linux; Android 4.1.1; Galaxy Nexus Build/JRO03C) AppleWebKit/535.19 (KHTML, like Gecko) Chrome/18.0.1025.166 Mobile Safari/535.19"
ACCEPT_HEADER_ALL: str = "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.9"

SUPPORTED_LANGUAGES_LIST = [
    "en",   # English
    "de",   # German
    "fr",   # French
    "it",   # Italian
    "es",   # Spanish
    "sv",   # Swedish
    "nl",   # Dutch
    "no",   # Norwegian
    "cs",   # Czech
    "sk",   # Slovak
    "hu",   # Hungarian
    "da",   # Danish
    "pl",   # Polish
    "fi",   # Finnish
    "pt"    # Portuguese
]


def _check_response_for_errors(response: dict) -> None:
    """
    Checks for errors in the API response. If an error is found, an exception is raised.
    retCode known values:
    - S: success
    - F: failure
    resCode / resMsg known values:
    - 0000: no error
    - 4004: "Duplicate request"
    - 4081: "Request timeout"
    - 5031: "Unavailable remote control - Service Temporary Unavailable"
    - 5091: "Exceeds number of requests"
    - 5921: "No Data Found v2 - No Data Found v2"
    :param response: the API's JSON response
    """

    error_code_mapping = {
        "4004": DuplicateRequestError,
        "4081": RequestTimeoutError,
        "5031": APIError,
        "5091": RateLimitingError,
        "5921": None
    }

    if not any(x in response for x in ["retCode", "resCode", "resMsg"]):
        _LOGGER.error(f"Unknown API response format: {response}")
        raise InvalidAPIResponseError()

    if response["retCode"] == "F":
        if response["resCode"] in error_code_mapping:
            if response["resCode"] == "5921":
                _LOGGER.error(f"{DOMAIN} - No Data Found, car may be offline")
            else:
                raise error_code_mapping[response["resCode"]](response["resMsg"])
        else:
            raise APIError(f"Server returned: '{response['resMsg']}'")


class KiaUvoApiEU(ApiImpl):
    data_timezone = tz.gettz("Europe/Berlin")
    temperature_range = [x * 0.5 for x in range(28, 60)]

    def __init__(self, region: int, brand: int, language: str) -> None:
        self.stamps = None

        if language not in SUPPORTED_LANGUAGES_LIST:
            _LOGGER.warning(f"Unsupported language: {language}, fallback to en")
            language = "en"  # fallback to English
        self.LANGUAGE: str = language

        if BRANDS[brand] == BRAND_KIA:
            self.BASE_DOMAIN: str = "prd.eu-ccapi.kia.com"
            self.CCSP_SERVICE_ID: str = "fdc85c00-0a2f-4c64-bcb4-2cfb1500730a"
            self.APP_ID: str = "e7bcd186-a5fd-410d-92cb-6876a42288bd"
            self.BASIC_AUTHORIZATION: str = (
                "Basic ZmRjODVjMDAtMGEyZi00YzY0LWJjYjQtMmNmYjE1MDA3MzBhOnNlY3JldA=="
            )
            self.LOGIN_FORM_HOST = "eu-account.kia.com"
        elif BRANDS[brand] == BRAND_HYUNDAI:
            self.BASE_DOMAIN: str = "prd.eu-ccapi.hyundai.com"
            self.CCSP_SERVICE_ID: str = "6d477c38-3ca4-4cf3-9557-2a1929a94654"
            self.APP_ID: str = "014d2225-8495-4735-812d-2616334fd15d"
            self.BASIC_AUTHORIZATION: str = "Basic NmQ0NzdjMzgtM2NhNC00Y2YzLTk1NTctMmExOTI5YTk0NjU0OktVeTQ5WHhQekxwTHVvSzB4aEJDNzdXNlZYaG10UVI5aVFobUlGampvWTRJcHhzVg=="
            self.LOGIN_FORM_HOST = "eu-account.hyundai.com"

        self.BASE_URL: str = self.BASE_DOMAIN + ":8080"
        self.USER_API_URL: str = "https://" + self.BASE_URL + "/api/v1/user/"
        self.SPA_API_URL: str = "https://" + self.BASE_URL + "/api/v1/spa/"
        self.SPA_API_URL_V2: str = "https://" + self.BASE_URL + "/api/v2/spa/"

        self.CLIENT_ID: str = self.CCSP_SERVICE_ID
        self.GCM_SENDER_ID = 199360397125

        if BRANDS[brand] == BRAND_KIA:
            auth_client_id = "f4d531c7-1043-444d-b09a-ad24bd913dd4"
            self.LOGIN_FORM_URL: str = (
                "https://"
                + self.LOGIN_FORM_HOST
                + "/auth/realms/eukiaidm/protocol/openid-connect/auth?client_id="
                + auth_client_id
                + "&scope=openid%20profile%20email%20phone&response_type=code&hkid_session_reset=true&redirect_uri="
                + self.USER_API_URL
                + "integration/redirect/login&ui_locales=" + self.LANGUAGE + "&state=$service_id:$user_id"
            )
        elif BRANDS[brand] == BRAND_HYUNDAI:
            auth_client_id = "64621b96-0f0d-11ec-82a8-0242ac130003"
            self.LOGIN_FORM_URL: str = (
                "https://"
                + self.LOGIN_FORM_HOST
                + "/auth/realms/euhyundaiidm/protocol/openid-connect/auth?client_id="
                + auth_client_id
                + "&scope=openid%20profile%20email%20phone&response_type=code&hkid_session_reset=true&redirect_uri="
                + self.USER_API_URL
                + "integration/redirect/login&ui_locales=" + self.LANGUAGE + "&state=$service_id:$user_id"
            )

        self.stamps_url: str = (
            "https://raw.githubusercontent.com/neoPix/bluelinky-stamps/master/"
            + BRANDS[brand].lower()
            + "-"
            + self.APP_ID
            + ".v2.json"
        )

    def _get_authenticated_headers(self, token: Token) -> dict:
        return {
            "Authorization": token.access_token,
            "ccsp-service-id": self.CCSP_SERVICE_ID,
            "ccsp-application-id": self.APP_ID,
            "Stamp": self._get_stamp(),
            "ccsp-device-id": token.device_id,
            "Host": self.BASE_URL,
            "Connection": "Keep-Alive",
            "Accept-Encoding": "gzip",
            "User-Agent": USER_AGENT_OK_HTTP,
        }

    def login(self, username: str, password: str) -> Token:

        stamp = self._get_stamp()
        device_id = self._get_device_id(stamp)
        cookies = self._get_cookies()
        self._set_session_language(cookies)
        authorization_code = None
        try:
            authorization_code = self._get_authorization_code_with_redirect_url(
                username, password, cookies
            )
        except Exception as ex1:
            _LOGGER.debug(f"{DOMAIN} - get_authorization_code_with_redirect_url failed")
            authorization_code = self._get_authorization_code_with_form(
                username, password, cookies
            )

        if authorization_code is None:
            raise AuthenticationError("Login Failed")
        (
            token_type,
            access_token,
            authorization_code,
        ) = self._get_access_token(stamp, authorization_code)

        token_type, refresh_token = self._get_refresh_token(stamp, authorization_code)
        valid_until = dt.datetime.now(pytz.utc) + dt.timedelta(hours=23)

        return Token(
            username=username,
            password=password,
            access_token=access_token,
            refresh_token=refresh_token,
            device_id=device_id,
            stamp=stamp,
            valid_until=valid_until,
        )

    def get_vehicles(self, token: Token) -> list[Vehicle]:
        url = self.SPA_API_URL + "vehicles"
        response = requests.get(url, headers=self._get_authenticated_headers(token)).json()
        _LOGGER.debug(f"{DOMAIN} - Get Vehicles Response: {response}")
        _check_response_for_errors(response)
        result = []
        for entry in response["resMsg"]["vehicles"]:
            entry_engine_type = None
            if(entry["type"] == "GN"):
                entry_engine_type = ENGINE_TYPES.ICE
            elif(entry["type"] == "EV"):
                entry_engine_type = ENGINE_TYPES.EV
            elif(entry["type"] == "PHEV"):
                entry_engine_type = ENGINE_TYPES.PHEV
            elif(entry["type"] == "HV"):
                entry_engine_type = ENGINE_TYPES.HEV
            vehicle: Vehicle = Vehicle(
                id=entry["vehicleId"],
                name=entry["nickname"],
                model=entry["vehicleName"],
                registration_date=entry["regDate"],
                VIN=entry["vin"],
                engine_type=entry_engine_type,
            )
            result.append(vehicle)
        return result

    def get_last_updated_at(self, value) -> dt.datetime:
        if value is not None:
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

    def update_vehicle_with_cached_state(self, token: Token, vehicle: Vehicle) -> None:
        state = self._get_cached_vehicle_state(token, vehicle)
        self._update_vehicle_properties(vehicle, state)

        if vehicle.engine_type == ENGINE_TYPES.EV:
            try:
                state = self._get_driving_info(token, vehicle)
            except Exception as e:
                # we don't know if all car types (ex: ICE cars) provide this information.
                # we also don't know what the API returns if the info is unavailable.
                # so, catch any exception and move on.
                _LOGGER.exception("""Failed to parse driving info. Possible reasons:
                                    - incompatible vehicle (ICE)
                                    - new API format
                                    - API outage
                            """, exc_info=e)
            else:
                self._update_vehicle_drive_info(vehicle, state)

    def force_refresh_vehicle_state(self, token: Token, vehicle: Vehicle) -> None:
        state = self._get_forced_vehicle_state(token, vehicle)
        state["vehicleLocation"] = self._get_location(token, vehicle)
        self._update_vehicle_properties(vehicle, state)
        #Only call for driving info on cars we know have a chance of supporting it.   Could be expanded if other types do support it.
        if vehicle.engine_type == ENGINE_TYPES.EV:
            try:
                state = self._get_driving_info(token, vehicle)
            except Exception as e:
                # we don't know if all car types provide this information.
                # we also don't know what the API returns if the info is unavailable.
                # so, catch any exception and move on.
                _LOGGER.exception("""Failed to parse driving info. Possible reasons:
                                    - new API format
                                    - API outage
                            """, exc_info=e)
            else:
                self._update_vehicle_drive_info(vehicle, state)

    def _update_vehicle_properties(self, vehicle: Vehicle, state: dict) -> None:
        if get_child_value(state, "vehicleStatus.time"):
            vehicle.last_updated_at = self.get_last_updated_at(
                get_child_value(state, "vehicleStatus.time")
            )
        else:
            vehicle.last_updated_at = dt.datetime.now(self.data_timezone)

        vehicle.total_driving_range = (
            get_child_value(
                state,
                "vehicleStatus.evStatus.drvDistance.0.rangeByFuel.totalAvailableRange.value",
            ),
            DISTANCE_UNITS[
                get_child_value(
                    state,
                    "vehicleStatus.evStatus.drvDistance.0.rangeByFuel.totalAvailableRange.unit",
                )
            ],
        )

        #Only update odometer if present.   It isn't present in a force update.  Dec 2022 update also reports 0 when the car is off.  This tries to remediate best we can.  Can be removed once fixed in the cars firmware.
        if get_child_value(state, "odometer.value") is not None:
            if get_child_value(state, "odometer.value") != 0:
                vehicle.odometer = (
                    get_child_value(state, "odometer.value"),
                    DISTANCE_UNITS[
                        get_child_value(
                            state,
                            "odometer.unit",
                        )
                    ],
                )
            elif vehicle.odometer is None:
                vehicle.odometer = (
                    get_child_value(state, "odometer.value"),
                    DISTANCE_UNITS[
                        get_child_value(
                            state,
                            "odometer.unit",
                        )
                    ],
                )
        vehicle.car_battery_percentage = get_child_value(
            state, "vehicleStatus.battery.batSoc"
        )
        vehicle.engine_is_running = get_child_value(state, "vehicleStatus.engine")

        # Converts temp to usable number. Currently only support celsius. Future to do is check unit in case the care itself is set to F.
        if get_child_value(state, "vehicleStatus.airTemp.value"):
            tempIndex = get_hex_temp_into_index(get_child_value(state, "vehicleStatus.airTemp.value"))

            vehicle.air_temperature = (
                self.temperature_range[tempIndex],
                TEMPERATURE_UNITS[
                    get_child_value(
                        state,
                        "vehicleStatus.airTemp.unit",
                    )
                ],
            )
        vehicle.defrost_is_on = get_child_value(state, "vehicleStatus.defrost")
        steer_wheel_heat = get_child_value(
            state, "vehicleStatus.steerWheelHeat"
        )
        if steer_wheel_heat in [0, 2]:
            vehicle.steering_wheel_heater_is_on = False
        elif steer_wheel_heat == 1:
            vehicle.steering_wheel_heater_is_on = True

        vehicle.back_window_heater_is_on = get_child_value(
            state, "vehicleStatus.sideBackWindowHeat"
        )
        vehicle.side_mirror_heater_is_on = get_child_value(
            state, "vehicleStatus.sideMirrorHeat"
        )
        vehicle.front_left_seat_status = SEAT_STATUS[get_child_value(
            state, "vehicleStatus.seatHeaterVentState.flSeatHeatState"
        )]
        vehicle.front_right_seat_status = SEAT_STATUS[get_child_value(
            state, "vehicleStatus.seatHeaterVentState.frSeatHeatState"
        )]
        vehicle.rear_left_seat_status = SEAT_STATUS[get_child_value(
            state, "vehicleStatus.seatHeaterVentState.rlSeatHeatState"
        )]
        vehicle.rear_right_seat_status = SEAT_STATUS[get_child_value(
            state, "vehicleStatus.seatHeaterVentState.rrSeatHeatState"
        )]
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
        vehicle.hood_is_open = get_child_value(
            state, "vehicleStatus.hoodOpen"
        )
        vehicle.tire_pressure_rear_left_warning_is_on = bool(get_child_value(
            state, "vehicleStatus.tirePressureLamp.tirePressureLampRL"
        ))
        vehicle.tire_pressure_front_left_warning_is_on = bool(get_child_value(
            state, "vehicleStatus.tirePressureLamp.tirePressureLampFL"
        ))
        vehicle.tire_pressure_front_right_warning_is_on = bool(get_child_value(
            state, "vehicleStatus.tirePressureLamp.tirePressureLampFR"
        ))
        vehicle.tire_pressure_rear_right_warning_is_on = bool(get_child_value(
            state, "vehicleStatus.tirePressureLamp.tirePressureLampRR"
        ))
        vehicle.tire_pressure_all_warning_is_on = bool(get_child_value(
            state, "vehicleStatus.tirePressureLamp.tirePressureLampAll"
        ))
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

        ev_charge_port_door_is_open = get_child_value(
            state, "vehicleStatus.evStatus.chargePortDoorOpenStatus"
        )

        if ev_charge_port_door_is_open == 1:
            vehicle.ev_charge_port_door_is_open = True
        elif ev_charge_port_door_is_open == 2:
            vehicle.ev_charge_port_door_is_open = False

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

        target_soc_list = get_child_value(
            state, "vehicleStatus.evStatus.reservChargeInfos.targetSOClist")
        try:
            vehicle.ev_charge_limits_ac = [x['targetSOClevel'] for x in target_soc_list if x['plugType'] == 1][-1]
            vehicle.ev_charge_limits_dc = [x['targetSOClevel'] for x in target_soc_list if x['plugType'] == 0][-1]
        except:
            _LOGGER.debug(f"{DOMAIN} - SOC Levels couldn't be found. May not be an EV.")
        if get_child_value(
                state,
                "vehicleStatus.evStatus.drvDistance.0.rangeByFuel.gasModeRange.value",
            ):
            vehicle.fuel_driving_range = (
                get_child_value(
                    state,
                    "vehicleStatus.evStatus.drvDistance.0.rangeByFuel.gasModeRange.value",
                ),
                DISTANCE_UNITS[
                    get_child_value(
                        state,
                        "vehicleStatus.evStatus.drvDistance.0.rangeByFuel.gasModeRange.unit",
                    )
                ],
            )
        elif get_child_value(
                state,
                "vehicleStatus.dte.value",
            ):
            vehicle.fuel_driving_range = (
                get_child_value(
                    state,
                    "vehicleStatus.dte.value",
                ),
                DISTANCE_UNITS[get_child_value(state, "vehicleStatus.dte.unit")],
            )

        vehicle.ev_target_range_charge_AC = (
            get_child_value(
                state,
                "vehicleStatus.evStatus.reservChargeInfos.targetSOClist.1.dte.rangeByFuel.totalAvailableRange.value",
            ),
            DISTANCE_UNITS[
                get_child_value(
                    state,
                    "vehicleStatus.evStatus.reservChargeInfos.targetSOClist.1.dte.rangeByFuel.totalAvailableRange.unit",
                )
            ],
        )
        vehicle.ev_target_range_charge_DC = (
            get_child_value(
                state,
                "vehicleStatus.evStatus.reservChargeInfos.targetSOClist.0.dte.rangeByFuel.totalAvailableRange.value",
            ),
            DISTANCE_UNITS[
                get_child_value(
                    state,
                    "vehicleStatus.evStatus.reservChargeInfos.targetSOClist.0.dte.rangeByFuel.totalAvailableRange.unit",
                )
            ],
        )

        vehicle.washer_fluid_warning_is_on = get_child_value(state, "vehicleStatus.washerFluidStatus")
        vehicle.fuel_level = get_child_value(state, "vehicleStatus.fuelLevel")
        vehicle.fuel_level_is_low = get_child_value(state, "vehicleStatus.lowFuelLight")
        vehicle.air_control_is_on = get_child_value(state, "vehicleStatus.airCtrlOn")
        vehicle.smart_key_battery_warning_is_on = get_child_value(state, "vehicleStatus.smartKeyBatteryWarning")


        if get_child_value(state, "vehicleLocation.coord.lat"):
            vehicle.location = (
                get_child_value(state, "vehicleLocation.coord.lat"),
                get_child_value(state, "vehicleLocation.coord.lon"),
                self.get_last_updated_at(get_child_value(state, "vehicleLocation.time")),
            )
        vehicle.data = state

    def _update_vehicle_drive_info(self, vehicle: Vehicle, state: dict) -> None:
        vehicle.total_power_consumed = get_child_value(state, "totalPwrCsp")
        vehicle.power_consumption_30d = get_child_value(state, "consumption30d")
        vehicle.daily_stats = get_child_value(state, "dailyStats")

    def _get_cached_vehicle_state(self, token: Token, vehicle: Vehicle) -> dict:
        url = self.SPA_API_URL + "vehicles/" + vehicle.id + "/status/latest"

        response = requests.get(url, headers=self._get_authenticated_headers(token)).json()
        _LOGGER.debug(f"{DOMAIN} - get_cached_vehicle_status response: {response}")
        _check_response_for_errors(response)
        response = response["resMsg"]["vehicleStatusInfo"]

        return response

    def _get_location(self, token: Token, vehicle: Vehicle) -> dict:
        url = self.SPA_API_URL + "vehicles/" + vehicle.id + "/location"

        try:
            response = requests.get(url, headers=self._get_authenticated_headers(token)).json()
            _LOGGER.debug(f"{DOMAIN} - _get_location response: {response}")
            _check_response_for_errors(response)
            return response["resMsg"]["gpsDetail"]
        except:
            _LOGGER.warning(f"{DOMAIN} - _get_location failed")
            return None

    def _get_forced_vehicle_state(self, token: Token, vehicle: Vehicle) -> dict:
        url = self.SPA_API_URL + "vehicles/" + vehicle.id + "/status"
        response = requests.get(url, headers=self._get_authenticated_headers(token)).json()
        _LOGGER.debug(f"{DOMAIN} - Received forced vehicle data: {response}")
        _check_response_for_errors(response)
        mapped_response = {}
        mapped_response["vehicleStatus"] = response["resMsg"]
        return mapped_response

    def lock_action(self, token: Token, vehicle: Vehicle, action: VEHICLE_LOCK_ACTION) -> None:
        url = self.SPA_API_URL + "vehicles/" + vehicle.id + "/control/door"

        payload = {"action": action.value, "deviceId": token.device_id}
        _LOGGER.debug(f"{DOMAIN} - Lock Action Request: {payload}")
        response = requests.post(url, json=payload, headers=self._get_authenticated_headers(token)).json()
        _LOGGER.debug(f"{DOMAIN} - Lock Action Response: {response}")
        _check_response_for_errors(response)

    def charge_port_action(self, token: Token, vehicle: Vehicle, action: CHARGE_PORT_ACTION) -> None:
        url = self.SPA_API_URL_V2 + "vehicles/" + vehicle.id + "/control/portdoor"

        payload = {"action": action.value, "deviceId": token.device_id}
        _LOGGER.debug(f"{DOMAIN} - Charge Port Action Request: {payload}")
        response = requests.post(url, json=payload, headers=self._get_authenticated_headers(token)).json()
        _LOGGER.debug(f"{DOMAIN} - Charge Port Action Response: {response}")
        _check_response_for_errors(response)

    def start_climate(
        self, token: Token, vehicle: Vehicle, options: ClimateRequestOptions
    ) -> None:
        url = self.SPA_API_URL + "vehicles/" + vehicle.id + "/control/temperature"

        # Defaults are located here to be region specific

        if options.set_temp is None:
            options.set_temp = 21
        if options.duration is None:
            options.duration = 5
        if options.defrost is None:
            options.defrost = False
        if options.climate is None:
            options.climate = True
        if options.heating is None:
            options.heating = 0

        hex_set_temp = get_index_into_hex_temp(
            self.temperature_range.index(options.set_temp)
        )

        payload = {
            "action": "start",
            "hvacType": 0,
            "options": {
                "defrost": options.defrost,
                "heating1": int(options.heating),
            },
            "tempCode": hex_set_temp,
            "unit": "C",
        }
        _LOGGER.debug(f"{DOMAIN} - Start Climate Action Request: {payload}")
        response = requests.post(url, json=payload, headers=self._get_authenticated_headers(token)).json()
        _LOGGER.debug(f"{DOMAIN} - Start Climate Action Response: {response}")
        _check_response_for_errors(response)

    def stop_climate(self, token: Token, vehicle: Vehicle) -> None:
        url = self.SPA_API_URL + "vehicles/" + vehicle.id + "/control/temperature"

        payload = {
            "action": "stop",
            "hvacType": 0,
            "options": {
                "defrost": True,
                "heating1": 1,
            },
            "tempCode": "10H",
            "unit": "C",
        }
        _LOGGER.debug(f"{DOMAIN} - Stop Climate Action Request: {payload}")
        response = requests.post(url, json=payload, headers=self._get_authenticated_headers(token)).json()
        _LOGGER.debug(f"{DOMAIN} - Stop Climate Action Response: {response}")
        _check_response_for_errors(response)

    def start_charge(self, token: Token, vehicle: Vehicle) -> None:
        url = self.SPA_API_URL + "vehicles/" + vehicle.id + "/control/charge"

        payload = {"action": "start", "deviceId": token.device_id}
        _LOGGER.debug(f"{DOMAIN} - Start Charge Action Request: {payload}")
        response = requests.post(url, json=payload, headers=self._get_authenticated_headers(token)).json()
        _LOGGER.debug(f"{DOMAIN} - Start Charge Action Response: {response}")
        _check_response_for_errors(response)

    def stop_charge(self, token: Token, vehicle: Vehicle) -> None:
        url = self.SPA_API_URL + "vehicles/" + vehicle.id + "/control/charge"

        payload = {"action": "stop", "deviceId": token.device_id}
        _LOGGER.debug(f"{DOMAIN} - Stop Charge Action Request {payload}")
        response = requests.post(url, json=payload, headers=self._get_authenticated_headers(token)).json()
        _LOGGER.debug(f"{DOMAIN} - Stop Charge Action Response: {response}")
        _check_response_for_errors(response)

    def _get_charge_limits(self, token: Token, vehicle: Vehicle) -> dict:
        #Not currently used as value is in the general get.  Most likely this forces the car the update it.
        url = f"{self.SPA_API_URL}vehicles/{vehicle.id}/charge/target"

        _LOGGER.debug(f"{DOMAIN} - Get Charging Limits Request")
        response = requests.get(url, headers=self._get_authenticated_headers(token)).json()
        _LOGGER.debug(f"{DOMAIN} - Get Charging Limits Response: {response}")
        _check_response_for_errors()
        # API sometimes returns multiple entries per plug type and they conflict.
        # The car itself says the last entry per plug type is the truth when tested (EU Ioniq Electric Facelift MY 2019)
        if response['resMsg'] is not None:
            return response['resMsg']

    def _get_driving_info(self, token: Token, vehicle: Vehicle) -> dict:
        url = self.SPA_API_URL + "vehicles/" + vehicle.id + "/drvhistory"

        responseAlltime = requests.post(url, json={"periodTarget": 1}, headers=self._get_authenticated_headers(token))
        responseAlltime = responseAlltime.json()
        _LOGGER.debug(f"{DOMAIN} - get_driving_info responseAlltime {responseAlltime}")

        response30d = requests.post(url, json={"periodTarget": 0}, headers=self._get_authenticated_headers(token))
        response30d = response30d.json()
        _LOGGER.debug(f"{DOMAIN} - get_driving_info response30d {response30d}")
        if get_child_value(responseAlltime, "resMsg.drivingInfoDetail.0"):
            drivingInfo = responseAlltime["resMsg"]["drivingInfoDetail"][0]

            drivingInfo["dailyStats"] = []
            for day in response30d["resMsg"]["drivingInfoDetail"]:
                processedDay = DailyDrivingStats(
                    date=dt.datetime.strptime(day["drivingDate"], "%Y%m%d"),
                    total_consumed=day["totalPwrCsp"],
                    engine_consumption=day["motorPwrCsp"],
                    climate_consumption=day["climatePwrCsp"],
                    onboard_electronics_consumption=day["eDPwrCsp"],
                    battery_care_consumption=day["batteryMgPwrCsp"],
                    regenerated_energy=day["regenPwr"],
                    distance=day["calculativeOdo"]
                )
                drivingInfo["dailyStats"].append(processedDay)

            for drivingInfoItem in response30d["resMsg"]["drivingInfo"]:
                if drivingInfoItem["drivingPeriod"] == 0:
                    drivingInfo["consumption30d"] = round(
                        drivingInfoItem["totalPwrCsp"]
                        / drivingInfoItem["calculativeOdo"]
                    )
                    break

            return drivingInfo
        else:
            _LOGGER.debug(f"{DOMAIN} - Driving info didn't return valid data. This may be normal if the car doesn't support it.")
            return None

    def set_charge_limits(self, token: Token, vehicle: Vehicle, ac: int, dc: int)-> str:
        url = self.SPA_API_URL + "vehicles/" + vehicle.id + "/charge/target"

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
        response = requests.post(url, json=body, headers=self._get_authenticated_headers(token))
        _LOGGER.debug(f"{DOMAIN} - Set Charge Limits Response: {response}")

        return str(response.status_code == 200)

    def _get_stamp(self) -> str:
        if self.stamps is None:
            self.stamps = requests.get(self.stamps_url).json()

        frequency = self.stamps["frequency"]
        generated_at = parser.isoparse(self.stamps["generated"])
        position = int(
            (dt.datetime.now(pytz.utc) - generated_at).total_seconds()
            * 1000.0
            / frequency
        )
        stamp_count = len(self.stamps["stamps"])
        _LOGGER.debug(
            f"{DOMAIN} - get_stamp {generated_at} {frequency} {position} {stamp_count} {((dt.datetime.now(pytz.utc) - generated_at).total_seconds() * 1000.0) / frequency}"
        )
        if (position * 100.0) / stamp_count > 90:
            self.stamps = None
            return self._get_stamp()
        else:
            return self.stamps["stamps"][position]

    def _get_device_id(self, stamp: str):
        registration_id = 1
        url = self.SPA_API_URL + "notifications/register"
        payload = {
            "pushRegId": registration_id,
            "pushType": "GCM",
            "uuid": str(uuid.uuid4()),
        }

        headers = {
            "ccsp-service-id": self.CCSP_SERVICE_ID,
            "ccsp-application-id": self.APP_ID,
            "Stamp": stamp,
            "Content-Type": "application/json;charset=UTF-8",
            "Host": self.BASE_URL,
            "Connection": "Keep-Alive",
            "Accept-Encoding": "gzip",
            "User-Agent": USER_AGENT_OK_HTTP,
        }

        response = requests.post(url, headers=headers, json=payload)
        response = response.json()
        _LOGGER.debug(f"{DOMAIN} - Get Device ID request: {headers} {payload}")
        _LOGGER.debug(f"{DOMAIN} - Get Device ID response: {response}")
        device_id = response["resMsg"]["deviceId"]
        return device_id

    def _get_cookies(self) -> dict:
        ### Get Cookies ###
        url = (
            self.USER_API_URL
            + "oauth2/authorize?response_type=code&state=test&client_id="
            + self.CLIENT_ID
            + "&redirect_uri="
            + self.USER_API_URL
            + "oauth2/redirect&lang=" + self.LANGUAGE
        )
        payload = {}
        headers = {
            "Host": self.BASE_URL,
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
            "User-Agent": USER_AGENT_MOZILLA,
            "Accept": ACCEPT_HEADER_ALL,
            "X-Requested-With": "com.kia.uvo.eu",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-User": "?1",
            "Sec-Fetch-Dest": "document",
            "Accept-Encoding": "gzip, deflate",
            "Accept-Language": "en,en-US," + self.LANGUAGE + ";q=0.9",
        }

        _LOGGER.debug(f"{DOMAIN} - Get cookies request: {url}")
        session = requests.Session()
        response = session.get(url)
        _LOGGER.debug(f"{DOMAIN} - Get cookies response: {session.cookies.get_dict()}")
        return session.cookies.get_dict()
        # return session

    def _set_session_language(self, cookies) -> None:
        ### Set Language for Session ###
        url = self.USER_API_URL + "language"
        headers = {"Content-type": "application/json"}
        payload = {"lang": self.LANGUAGE}
        response = requests.post(url, json=payload, headers=headers, cookies=cookies)

    def _get_authorization_code_with_redirect_url(
        self, username, password, cookies
    ) -> str:
        url = self.USER_API_URL + "signin"
        headers = {"Content-type": "application/json"}
        data = {"email": username, "password": password}
        response = requests.post(
            url, json=data, headers=headers, cookies=cookies
        ).json()
        _LOGGER.debug(f"{DOMAIN} - Sign In Response: {response}")
        parsed_url = urlparse(response["redirectUrl"])
        authorization_code = "".join(parse_qs(parsed_url.query)["code"])
        return authorization_code

    def _get_authorization_code_with_form(self, username, password, cookies) -> str:
        url = self.USER_API_URL + "integrationinfo"
        headers = {"User-Agent": USER_AGENT_MOZILLA}
        response = requests.get(url, headers=headers, cookies=cookies)
        cookies = cookies | response.cookies.get_dict()
        response = response.json()
        _LOGGER.debug(f"{DOMAIN} - IntegrationInfo Response: {response}")
        user_id = response["userId"]
        service_id = response["serviceId"]

        login_form_url = self.LOGIN_FORM_URL
        login_form_url = login_form_url.replace("$service_id", service_id)
        login_form_url = login_form_url.replace("$user_id", user_id)

        response = requests.get(login_form_url, headers=headers, cookies=cookies)
        cookies = cookies | response.cookies.get_dict()
        _LOGGER.debug(
            f"{DOMAIN} - LoginForm {login_form_url} - Response: {response.text}"
        )
        soup = BeautifulSoup(response.content, "html.parser")
        login_form_action_url = soup.find("form")["action"].replace("&amp;", "&")

        data = {
            "username": username,
            "password": password,
            "credentialId": "",
            "rememberMe": "on",
        }
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": USER_AGENT_MOZILLA,
        }
        response = requests.post(
            login_form_action_url,
            data=data,
            headers=headers,
            allow_redirects=False,
            cookies=cookies,
        )
        cookies = cookies | response.cookies.get_dict()
        _LOGGER.debug(
            f"{DOMAIN} - LoginFormSubmit {login_form_action_url} - Response {response.status_code} - {response.headers}"
        )
        if response.status_code != 302:
            _LOGGER.debug(
                f"{DOMAIN} - LoginFormSubmit Error {login_form_action_url} - Response {response.status_code} - {response.text}"
            )
            return

        redirect_url = response.headers["Location"]
        headers = {"User-Agent": USER_AGENT_MOZILLA}
        response = requests.get(redirect_url, headers=headers, cookies=cookies)
        cookies = cookies | response.cookies.get_dict()
        _LOGGER.debug(
            f"{DOMAIN} - Redirect User Id {redirect_url} - Response {response.url} - {response.text}"
        )

        intUserId = 0
        if "account-find-link" in response.text:
            soup = BeautifulSoup(response.content, "html.parser")
            login_form_action_url = soup.find("form")["action"].replace("&amp;", "&")
            data = {"actionType": "FIND", "createToUVO": "UVO", "email": ""}
            headers = {
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": USER_AGENT_MOZILLA,
            }
            response = requests.post(
                login_form_action_url,
                data=data,
                headers=headers,
                allow_redirects=False,
                cookies=cookies,
            )

            if response.status_code != 302:
                _LOGGER.debug(
                    f"{DOMAIN} - AccountFindLink Error {login_form_action_url} - Response {response.status_code}"
                )
                return

            cookies = cookies | response.cookies.get_dict()
            redirect_url = response.headers["Location"]
            headers = {"User-Agent": USER_AGENT_MOZILLA}
            response = requests.get(redirect_url, headers=headers, cookies=cookies)
            _LOGGER.debug(
                f"{DOMAIN} - Redirect User Id 2 {redirect_url} - Response {response.url}"
            )
            _LOGGER.debug(f"{DOMAIN} - Redirect 2 - Response Text {response.text}")
            parsed_url = urlparse(response.url)
            intUserId = "".join(parse_qs(parsed_url.query)["int_user_id"])
        else:
            parsed_url = urlparse(response.url)
            intUserId = "".join(parse_qs(parsed_url.query)["intUserId"])

        url = self.USER_API_URL + "silentsignin"
        headers = {
            "User-Agent": USER_AGENT_MOZILLA,
            "ccsp-service-id": self.CCSP_SERVICE_ID,
        }
        response = requests.post(
            url, headers=headers, json={"intUserId": intUserId}, cookies=cookies
        ).json()
        _LOGGER.debug(f"{DOMAIN} - silentsignin Response {response}")
        parsed_url = urlparse(response["redirectUrl"])
        authorization_code = "".join(parse_qs(parsed_url.query)["code"])
        return authorization_code

    def _get_access_token(self, stamp, authorization_code):
        ### Get Access Token ###
        url = self.USER_API_URL + "oauth2/token"
        headers = {
            "Authorization": self.BASIC_AUTHORIZATION,
            "Stamp": stamp,
            "Content-type": "application/x-www-form-urlencoded",
            "Host": self.BASE_URL,
            "Connection": "close",
            "Accept-Encoding": "gzip, deflate",
            "User-Agent": USER_AGENT_OK_HTTP,
        }

        data = (
            "grant_type=authorization_code&redirect_uri=https%3A%2F%2F"
            + self.BASE_DOMAIN
            + "%3A8080%2Fapi%2Fv1%2Fuser%2Foauth2%2Fredirect&code="
            + authorization_code
        )
        _LOGGER.debug(f"{DOMAIN} - Get Access Token Data: {headers}{data}")
        response = requests.post(url, data=data, headers=headers)
        response = response.json()
        _LOGGER.debug(f"{DOMAIN} - Get Access Token Response: {response}")

        token_type = response["token_type"]
        access_token = token_type + " " + response["access_token"]
        authorization_code = response["refresh_token"]
        _LOGGER.debug(f"{DOMAIN} - Access Token Value {access_token}")
        return token_type, access_token, authorization_code

    def _get_refresh_token(self, stamp, authorization_code):
        ### Get Refresh Token ###
        url = self.USER_API_URL + "oauth2/token"
        headers = {
            "Authorization": self.BASIC_AUTHORIZATION,
            "Stamp": stamp,
            "Content-type": "application/x-www-form-urlencoded",
            "Host": self.BASE_URL,
            "Connection": "close",
            "Accept-Encoding": "gzip, deflate",
            "User-Agent": USER_AGENT_OK_HTTP,
        }

        data = (
            "grant_type=refresh_token&redirect_uri=https%3A%2F%2Fwww.getpostman.com%2Foauth2%2Fcallback&refresh_token="
            + authorization_code
        )
        _LOGGER.debug(f"{DOMAIN} - Get Refresh Token Data: {data}")
        response = requests.post(url, data=data, headers=headers)
        response = response.json()
        _LOGGER.debug(f"{DOMAIN} - Get Refresh Token Response: {response}")
        token_type = response["token_type"]
        refresh_token = token_type + " " + response["access_token"]
        return token_type, refresh_token
