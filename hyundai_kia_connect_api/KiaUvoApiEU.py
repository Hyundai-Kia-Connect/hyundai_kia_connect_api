"""KiaUvoApiEU.py"""

# pylint:disable=missing-timeout,missing-class-docstring,missing-function-docstring,wildcard-import,unused-wildcard-import,invalid-name,logging-fstring-interpolation,broad-except,bare-except,super-init-not-called,unused-argument,line-too-long,too-many-lines

import base64
import random
import datetime as dt
import logging
import uuid
import math
from time import sleep
from urllib.parse import parse_qs, urlparse

import pytz
import requests
from bs4 import BeautifulSoup
from dateutil import tz

from .ApiImpl import (
    ClimateRequestOptions,
    ScheduleChargingClimateRequestOptions,
)
from .ApiImplType1 import ApiImplType1
from .ApiImplType1 import _check_response_for_errors

from .Token import Token
from .Vehicle import (
    Vehicle,
    DailyDrivingStats,
    MonthTripInfo,
    DayTripInfo,
    TripInfo,
    DayTripCounts,
)
from .const import (
    BRAND_GENESIS,
    BRAND_HYUNDAI,
    BRAND_KIA,
    BRANDS,
    CHARGE_PORT_ACTION,
    DISTANCE_UNITS,
    DOMAIN,
    ENGINE_TYPES,
    LOGIN_TOKEN_LIFETIME,
    OrderStatus,
    SEAT_STATUS,
    TEMPERATURE_UNITS,
    VEHICLE_LOCK_ACTION,
    VALET_MODE_ACTION,
)
from .exceptions import (
    AuthenticationError,
    APIError,
)
from .utils import (
    get_child_value,
    get_index_into_hex_temp,
    get_hex_temp_into_index,
    parse_datetime,
)

_LOGGER = logging.getLogger(__name__)

USER_AGENT_OK_HTTP: str = "okhttp/3.12.0"
USER_AGENT_MOZILLA: str = "Mozilla/5.0 (Linux; Android 4.1.1; Galaxy Nexus Build/JRO03C) AppleWebKit/535.19 (KHTML, like Gecko) Chrome/18.0.1025.166 Mobile Safari/535.19"  # noqa
ACCEPT_HEADER_ALL: str = "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.9"  # noqa

SUPPORTED_LANGUAGES_LIST = [
    "en",  # English
    "de",  # German
    "fr",  # French
    "it",  # Italian
    "es",  # Spanish
    "sv",  # Swedish
    "nl",  # Dutch
    "no",  # Norwegian
    "cs",  # Czech
    "sk",  # Slovak
    "hu",  # Hungarian
    "da",  # Danish
    "pl",  # Polish
    "fi",  # Finnish
    "pt",  # Portuguese
]


class KiaUvoApiEU(ApiImplType1):
    data_timezone = tz.gettz("Europe/Berlin")
    temperature_range = [x * 0.5 for x in range(28, 60)]

    def __init__(self, region: int, brand: int, language: str) -> None:
        language = language.lower()
        # Strip language variants (e.g. en-Gb)
        if len(language) > 2:
            language = language[0:2]
        if language not in SUPPORTED_LANGUAGES_LIST:
            _LOGGER.warning(f"Unsupported language: {language}, fallback to en")
            language = "en"  # fallback to English
        self.LANGUAGE: str = language
        self.brand: int = brand

        if BRANDS[self.brand] == BRAND_KIA:
            self.BASE_DOMAIN: str = "prd.eu-ccapi.kia.com"
            self.PORT: int = 8080
            self.CCSP_SERVICE_ID: str = "fdc85c00-0a2f-4c64-bcb4-2cfb1500730a"
            self.APP_ID: str = "a2b8469b-30a3-4361-8e13-6fceea8fbe74"
            self.CFB: str = base64.b64decode(
                "wLTVxwidmH8CfJYBWSnHD6E0huk0ozdiuygB4hLkM5XCgzAL1Dk5sE36d/bx5PFMbZs="
            )
            self.BASIC_AUTHORIZATION: str = (
                "Basic ZmRjODVjMDAtMGEyZi00YzY0LWJjYjQtMmNmYjE1MDA3MzBhOnNlY3JldA=="
            )
            self.LOGIN_FORM_HOST = "eu-account.kia.com"
            self.PUSH_TYPE = "APNS"
        elif BRANDS[self.brand] == BRAND_HYUNDAI:
            self.BASE_DOMAIN: str = "prd.eu-ccapi.hyundai.com"
            self.PORT: int = 8080
            self.CCSP_SERVICE_ID: str = "6d477c38-3ca4-4cf3-9557-2a1929a94654"
            self.APP_ID: str = "014d2225-8495-4735-812d-2616334fd15d"
            self.CFB: str = base64.b64decode(
                "RFtoRq/vDXJmRndoZaZQyfOot7OrIqGVFj96iY2WL3yyH5Z/pUvlUhqmCxD2t+D65SQ="
            )
            self.BASIC_AUTHORIZATION: str = "Basic NmQ0NzdjMzgtM2NhNC00Y2YzLTk1NTctMmExOTI5YTk0NjU0OktVeTQ5WHhQekxwTHVvSzB4aEJDNzdXNlZYaG10UVI5aVFobUlGampvWTRJcHhzVg=="  # noqa
            self.LOGIN_FORM_HOST = "eu-account.hyundai.com"
            self.PUSH_TYPE = "GCM"
        elif BRANDS[self.brand] == BRAND_GENESIS:
            self.BASE_DOMAIN: str = "prd-eu-ccapi.genesis.com"
            self.PORT: int = 443
            self.CCSP_SERVICE_ID: str = "3020afa2-30ff-412a-aa51-d28fbe901e10"
            self.APP_ID: str = "f11f2b86-e0e7-4851-90df-5600b01d8b70"
            self.CFB: str = base64.b64decode(
                "RFtoRq/vDXJmRndoZaZQyYo3/qFLtVReW8P7utRPcc0ZxOzOELm9mexvviBk/qqIp4A="
            )
            self.BASIC_AUTHORIZATION: str = "Basic NmQ0NzdjMzgtM2NhNC00Y2YzLTk1NTctMmExOTI5YTk0NjU0OktVeTQ5WHhQekxwTHVvSzB4aEJDNzdXNlZYaG10UVI5aVFobUlGampvWTRJcHhzVg=="  # noqa
            self.LOGIN_FORM_HOST = "accounts-eu.genesis.com"
            self.PUSH_TYPE = "GCM"

        self.BASE_URL: str = self.BASE_DOMAIN + ":" + str(self.PORT)
        self.USER_API_URL: str = "https://" + self.BASE_URL + "/api/v1/user/"
        self.SPA_API_URL: str = "https://" + self.BASE_URL + "/api/v1/spa/"
        self.SPA_API_URL_V2: str = "https://" + self.BASE_URL + "/api/v2/spa/"

        self.CLIENT_ID: str = self.CCSP_SERVICE_ID
        self.GCM_SENDER_ID = 199360397125

        if BRANDS[self.brand] == BRAND_KIA:
            auth_client_id = "572e0304-5f8d-4b4c-9dd5-41aa84eed160"
            self.LOGIN_FORM_URL: str = (
                "https://"
                + self.LOGIN_FORM_HOST
                + "/auth/realms/eukiaidm/protocol/openid-connect/auth?client_id="
                + auth_client_id
                + "&scope=openid%20profile%20email%20phone&response_type=code&hkid_session_reset=true&redirect_uri="  # noqa
                + self.USER_API_URL
                + "integration/redirect/login&ui_locales="
                + self.LANGUAGE
                + "&state=$service_id:$user_id"
            )
        elif BRANDS[self.brand] == BRAND_HYUNDAI:
            auth_client_id = "64621b96-0f0d-11ec-82a8-0242ac130003"
            self.LOGIN_FORM_URL: str = (
                "https://"
                + self.LOGIN_FORM_HOST
                + "/auth/realms/euhyundaiidm/protocol/openid-connect/auth?client_id="
                + auth_client_id
                + "&scope=openid%20profile%20email%20phone&response_type=code&hkid_session_reset=true&redirect_uri="  # noqa
                + self.USER_API_URL
                + "integration/redirect/login&ui_locales="
                + self.LANGUAGE
                + "&state=$service_id:$user_id"
            )
        elif BRANDS[self.brand] == BRAND_GENESIS:
            auth_client_id = "3020afa2-30ff-412a-aa51-d28fbe901e10"
            self.LOGIN_FORM_URL: str = (
                "https://"
                + self.LOGIN_FORM_HOST
                + "/auth/realms/eugenesisidm/protocol/openid-connect/auth?client_id="
                + auth_client_id
                + "&scope=openid%20profile%20email%20phone&response_type=code&hkid_session_reset=true&redirect_uri="  # noqa
                + self.USER_API_URL
                + "integration/redirect/login&ui_locales="
                + self.LANGUAGE
                + "&state=$service_id:$user_id"
            )

    def _get_control_headers(self, token: Token, vehicle: Vehicle) -> dict:
        control_token, _ = self._get_control_token(token)
        authenticated_headers = self._get_authenticated_headers(
            token, vehicle.ccu_ccs2_protocol_support
        )
        return authenticated_headers | {
            "Authorization": control_token,
            "AuthorizationCCSP": control_token,
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
        except Exception:
            _LOGGER.debug(f"{DOMAIN} - get_authorization_code_with_redirect_url failed")
            authorization_code = self._get_authorization_code_with_form(
                username, password, cookies
            )

        if authorization_code is None:
            raise AuthenticationError("Login Failed")

        _, access_token, authorization_code = self._get_access_token(
            stamp, authorization_code
        )
        _, refresh_token = self._get_refresh_token(stamp, authorization_code)
        valid_until = dt.datetime.now(pytz.utc) + LOGIN_TOKEN_LIFETIME

        return Token(
            username=username,
            password=password,
            access_token=access_token,
            refresh_token=refresh_token,
            device_id=device_id,
            valid_until=valid_until,
        )

    def get_vehicles(self, token: Token) -> list[Vehicle]:
        url = self.SPA_API_URL + "vehicles"
        response = requests.get(
            url,
            headers=self._get_authenticated_headers(token),
        ).json()
        _LOGGER.debug(f"{DOMAIN} - Get Vehicles Response: {response}")
        _check_response_for_errors(response)
        result = []
        for entry in response["resMsg"]["vehicles"]:
            entry_engine_type = None
            if entry["type"] == "GN":
                entry_engine_type = ENGINE_TYPES.ICE
            elif entry["type"] == "EV":
                entry_engine_type = ENGINE_TYPES.EV
            elif entry["type"] == "PHEV":
                entry_engine_type = ENGINE_TYPES.PHEV
            elif entry["type"] == "HV":
                entry_engine_type = ENGINE_TYPES.HEV
            elif entry["type"] == "PE":
                entry_engine_type = ENGINE_TYPES.PHEV
            vehicle: Vehicle = Vehicle(
                id=entry["vehicleId"],
                name=entry["nickname"],
                model=entry["vehicleName"],
                registration_date=entry["regDate"],
                VIN=entry["vin"],
                timezone=self.data_timezone,
                engine_type=entry_engine_type,
                ccu_ccs2_protocol_support=entry["ccuCCS2ProtocolSupport"],
            )
            result.append(vehicle)
        return result

    def _get_time_from_string(self, value, timesection) -> dt.datetime.time:
        if value is not None:
            lastTwo = int(value[-2:])
            if lastTwo > 60:
                value = int(value) + 40
            if int(value) > 1260:
                value = dt.datetime.strptime(str(value), "%H%M").time()
            else:
                d = dt.datetime.strptime(str(value), "%I%M")
                if timesection > 0:
                    d += dt.timedelta(hours=12)
                value = d.time()
        return value

    def update_vehicle_with_cached_state(self, token: Token, vehicle: Vehicle) -> None:
        url = self.SPA_API_URL + "vehicles/" + vehicle.id
        is_ccs2 = vehicle.ccu_ccs2_protocol_support != 0
        if is_ccs2:
            url += "/ccs2/carstatus/latest"
        else:
            url += "/status/latest"

        response = requests.get(
            url,
            headers=self._get_authenticated_headers(
                token, vehicle.ccu_ccs2_protocol_support
            ),
        ).json()

        _LOGGER.debug(f"{DOMAIN} - get_cached_vehicle_status response: {response}")
        _check_response_for_errors(response)

        if vehicle.ccu_ccs2_protocol_support == 0:
            self._update_vehicle_properties(
                vehicle, response["resMsg"]["vehicleStatusInfo"]
            )
        else:
            state = response["resMsg"]["state"]["Vehicle"]
            self._update_vehicle_properties_ccs2(vehicle, state)

        if (
            vehicle.engine_type == ENGINE_TYPES.EV
            or vehicle.engine_type == ENGINE_TYPES.PHEV
        ):
            try:
                state = self._get_driving_info(token, vehicle)
            except Exception as e:
                # we don't know if all car types (ex: ICE cars) provide this
                # information. We also don't know what the API returns if
                # the info is unavailable. So, catch any exception and move on.
                _LOGGER.exception(
                    """Failed to parse driving info. Possible reasons:
                                    - incompatible vehicle (ICE)
                                    - new API format
                                    - API outage
                            """,
                    exc_info=e,
                )
            else:
                self._update_vehicle_drive_info(vehicle, state)

    def force_refresh_vehicle_state(self, token: Token, vehicle: Vehicle) -> None:
        state = self._get_forced_vehicle_state(token, vehicle)
        state["vehicleLocation"] = self._get_location(token, vehicle)
        self._update_vehicle_properties(vehicle, state)
        # Only call for driving info on cars we know have a chance of supporting it.
        # Could be expanded if other types do support it.
        if (
            vehicle.engine_type == ENGINE_TYPES.EV
            or vehicle.engine_type == ENGINE_TYPES.PHEV
        ):
            try:
                state = self._get_driving_info(token, vehicle)
            except Exception as e:
                # we don't know if all car types provide this information.
                # we also don't know what the API returns if the info is unavailable.
                # so, catch any exception and move on.
                _LOGGER.exception(
                    """Failed to parse driving info. Possible reasons:
                                    - new API format
                                    - API outage
                            """,
                    exc_info=e,
                )
            else:
                self._update_vehicle_drive_info(vehicle, state)

    def _update_vehicle_properties(self, vehicle: Vehicle, state: dict) -> None:
        if get_child_value(state, "vehicleStatus.time"):
            vehicle.last_updated_at = parse_datetime(
                get_child_value(state, "vehicleStatus.time"), self.data_timezone
            )
        else:
            vehicle.last_updated_at = dt.datetime.now(self.data_timezone)
        if get_child_value(state, "odometer.value"):
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

        # Converts temp to usable number. Currently only support celsius.
        # Future to do is check unit in case the care itself is set to F.
        if get_child_value(state, "vehicleStatus.airTemp.value"):
            tempIndex = get_hex_temp_into_index(
                get_child_value(state, "vehicleStatus.airTemp.value")
            )

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
        steer_wheel_heat = get_child_value(state, "vehicleStatus.steerWheelHeat")
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
        vehicle.tire_pressure_rear_left_warning_is_on = bool(
            get_child_value(state, "vehicleStatus.tirePressureLamp.tirePressureLampRL")
        )
        vehicle.tire_pressure_front_left_warning_is_on = bool(
            get_child_value(state, "vehicleStatus.tirePressureLamp.tirePressureLampFL")
        )
        vehicle.tire_pressure_front_right_warning_is_on = bool(
            get_child_value(state, "vehicleStatus.tirePressureLamp.tirePressureLampFR")
        )
        vehicle.tire_pressure_rear_right_warning_is_on = bool(
            get_child_value(state, "vehicleStatus.tirePressureLamp.tirePressureLampRR")
        )
        vehicle.tire_pressure_all_warning_is_on = bool(
            get_child_value(state, "vehicleStatus.tirePressureLamp.tirePressureLampAll")
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

        ev_charge_port_door_is_open = get_child_value(
            state, "vehicleStatus.evStatus.chargePortDoorOpenStatus"
        )

        if ev_charge_port_door_is_open == 1:
            vehicle.ev_charge_port_door_is_open = True
        elif ev_charge_port_door_is_open == 2:
            vehicle.ev_charge_port_door_is_open = False

        vehicle.ev_charging_power = get_child_value(
            state, "vehicleStatus.evStatus.batteryPower.batteryStndChrgPower"
        )

        if (
            get_child_value(
                state,
                "vehicleStatus.evStatus.drvDistance.0.rangeByFuel.totalAvailableRange.value",  # noqa
            )
            is not None
        ):
            vehicle.total_driving_range = (
                round(
                    float(
                        get_child_value(
                            state,
                            "vehicleStatus.evStatus.drvDistance.0.rangeByFuel.totalAvailableRange.value",  # noqa
                        )
                    ),
                    1,
                ),
                DISTANCE_UNITS[
                    get_child_value(
                        state,
                        "vehicleStatus.evStatus.drvDistance.0.rangeByFuel.totalAvailableRange.unit",  # noqa
                    )
                ],
            )
        if (
            get_child_value(
                state,
                "vehicleStatus.evStatus.drvDistance.0.rangeByFuel.evModeRange.value",
            )
            is not None
        ):
            vehicle.ev_driving_range = (
                round(
                    float(
                        get_child_value(
                            state,
                            "vehicleStatus.evStatus.drvDistance.0.rangeByFuel.evModeRange.value",  # noqa
                        )
                    ),
                    1,
                ),
                DISTANCE_UNITS[
                    get_child_value(
                        state,
                        "vehicleStatus.evStatus.drvDistance.0.rangeByFuel.evModeRange.unit",  # noqa
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
            state, "vehicleStatus.evStatus.reservChargeInfos.targetSOClist"
        )
        try:
            vehicle.ev_charge_limits_ac = [
                x["targetSOClevel"] for x in target_soc_list if x["plugType"] == 1
            ][-1]
            vehicle.ev_charge_limits_dc = [
                x["targetSOClevel"] for x in target_soc_list if x["plugType"] == 0
            ][-1]
        except Exception:
            _LOGGER.debug(f"{DOMAIN} - SOC Levels couldn't be found. May not be an EV.")
        if (
            get_child_value(
                state,
                "vehicleStatus.evStatus.drvDistance.0.rangeByFuel.gasModeRange.value",
            )
            is not None
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
                "vehicleStatus.evStatus.reservChargeInfos.targetSOClist.1.dte.rangeByFuel.totalAvailableRange.value",  # noqa
            ),
            DISTANCE_UNITS[
                get_child_value(
                    state,
                    "vehicleStatus.evStatus.reservChargeInfos.targetSOClist.1.dte.rangeByFuel.totalAvailableRange.unit",  # noqa
                )
            ],
        )
        vehicle.ev_target_range_charge_DC = (
            get_child_value(
                state,
                "vehicleStatus.evStatus.reservChargeInfos.targetSOClist.0.dte.rangeByFuel.totalAvailableRange.value",  # noqa
            ),
            DISTANCE_UNITS[
                get_child_value(
                    state,
                    "vehicleStatus.evStatus.reservChargeInfos.targetSOClist.0.dte.rangeByFuel.totalAvailableRange.unit",  # noqa
                )
            ],
        )
        vehicle.ev_first_departure_enabled = get_child_value(
            state,
            "vehicleStatus.evStatus.reservChargeInfos.reservChargeInfo.reservChargeInfoDetail.reservChargeSet",  # noqa
        )
        vehicle.ev_second_departure_enabled = get_child_value(
            state,
            "vehicleStatus.evStatus.reservChargeInfos.reserveChargeInfo2.reservChargeInfoDetail.reservChargeSet",  # noqa
        )
        vehicle.ev_first_departure_days = get_child_value(
            state,
            "vehicleStatus.evStatus.reservChargeInfos.reservChargeInfo.reservChargeInfoDetail.reservInfo.day",  # noqa
        )
        vehicle.ev_second_departure_days = get_child_value(
            state,
            "vehicleStatus.evStatus.reservChargeInfos.reserveChargeInfo2.reservChargeInfoDetail.reservInfo.day",  # noqa
        )

        vehicle.ev_first_departure_time = self._get_time_from_string(
            get_child_value(
                state,
                "vehicleStatus.evStatus.reservChargeInfos.reservChargeInfo.reservChargeInfoDetail.reservInfo.time.time",  # noqa
            ),
            get_child_value(
                state,
                "vehicleStatus.evStatus.reservChargeInfos.reservChargeInfo.reservChargeInfoDetail.reservInfo.time.timeSection",  # noqa
            ),
        )

        vehicle.ev_second_departure_time = self._get_time_from_string(
            get_child_value(
                state,
                "vehicleStatus.evStatus.reservChargeInfos.reserveChargeInfo2.reservChargeInfoDetail.reservInfo.time.time",  # noqa
            ),
            get_child_value(
                state,
                "vehicleStatus.evStatus.reservChargeInfos.reserveChargeInfo2.reservChargeInfoDetail.reservInfo.time.timeSection",  # noqa
            ),
        )

        vehicle.ev_first_departure_climate_enabled = bool(
            get_child_value(
                state,
                "vehicleStatus.evStatus.reservChargeInfos.reservChargeInfo.reservChargeInfoDetail.reservFatcSet.airCtrl",  # noqa
            )
        )

        vehicle.ev_second_departure_climate_enabled = bool(
            get_child_value(
                state,
                "vehicleStatus.evStatus.reservChargeInfos.reserveChargeInfo2.reservChargeInfoDetail.reservFatcSet.airCtrl",  # noqa
            )
        )

        if get_child_value(
            state,
            "vehicleStatus.evStatus.reservChargeInfos.reservChargeInfo.reservChargeInfoDetail.reservFatcSet.airTemp.value",  # noqa
        ):
            temp_index = get_hex_temp_into_index(
                get_child_value(
                    state,
                    "vehicleStatus.evStatus.reservChargeInfos.reservChargeInfo.reservChargeInfoDetail.reservFatcSet.airTemp.value",  # noqa
                )
            )

            vehicle.ev_first_departure_climate_temperature = (
                self.temperature_range[temp_index],
                TEMPERATURE_UNITS[
                    get_child_value(
                        state,
                        "vehicleStatus.evStatus.reservChargeInfos.reservChargeInfo.reservChargeInfoDetail.reservFatcSet.airTemp.unit",  # noqa
                    )
                ],
            )

        if get_child_value(
            state,
            "vehicleStatus.evStatus.reservChargeInfos.reserveChargeInfo2.reservChargeInfoDetail.reservFatcSet.airTemp.value",  # noqa
        ):
            temp_index = get_hex_temp_into_index(
                get_child_value(
                    state,
                    "vehicleStatus.evStatus.reservChargeInfos.reserveChargeInfo2.reservChargeInfoDetail.reservFatcSet.airTemp.value",  # noqa
                )
            )

            vehicle.ev_second_departure_climate_temperature = (
                self.temperature_range[temp_index],
                TEMPERATURE_UNITS[
                    get_child_value(
                        state,
                        "vehicleStatus.evStatus.reservChargeInfos.reserveChargeInfo2.reservChargeInfoDetail.reservFatcSet.airTemp.unit",  # noqa
                    )
                ],
            )

        vehicle.ev_first_departure_climate_defrost = get_child_value(
            state,
            "vehicleStatus.evStatus.reservChargeInfos.reservChargeInfo.reservChargeInfoDetail.reservFatcSet.defrost",  # noqa
        )

        vehicle.ev_second_departure_climate_defrost = get_child_value(
            state,
            "vehicleStatus.evStatus.reservChargeInfos.reserveChargeInfo2.reservChargeInfoDetail.reservFatcSet.defrost",  # noqa
        )

        vehicle.ev_off_peak_start_time = self._get_time_from_string(
            get_child_value(
                state,
                "vehicleStatus.evStatus.reservChargeInfos.offpeakPowerInfo.offPeakPowerTime1.starttime.time",  # noqa
            ),
            get_child_value(
                state,
                "vehicleStatus.evStatus.reservChargeInfos.offpeakPowerInfo.offPeakPowerTime1.starttime.timeSection",  # noqa
            ),
        )

        vehicle.ev_off_peak_end_time = self._get_time_from_string(
            get_child_value(
                state,
                "vehicleStatus.evStatus.reservChargeInfos.offpeakPowerInfo.offPeakPowerTime1.endtime.time",  # noqa
            ),
            get_child_value(
                state,
                "vehicleStatus.evStatus.reservChargeInfos.offpeakPowerInfo.offPeakPowerTime1.endtime.timeSection",  # noqa
            ),
        )

        if get_child_value(
            state,
            "vehicleStatus.evStatus.reservChargeInfos.offpeakPowerInfo.offPeakPowerFlag",  # noqa
        ):
            if (
                get_child_value(
                    state,
                    "vehicleStatus.evStatus.reservChargeInfos.offpeakPowerInfo.offPeakPowerFlag",  # noqa
                )
                == 1
            ):
                vehicle.ev_off_peak_charge_only_enabled = True
            elif (
                get_child_value(
                    state,
                    "vehicleStatus.evStatus.reservChargeInfos.offpeakPowerInfo.offPeakPowerFlag",  # noqa
                )
                == 2
            ):
                vehicle.ev_off_peak_charge_only_enabled = False

        if (
            get_child_value(
                state,
                "vehicleStatus.evStatus.reservChargeInfos.reservFlag",  # noqa
            )
            == 1
        ):
            vehicle.ev_schedule_charge_enabled = True
        elif (
            get_child_value(
                state,
                "vehicleStatus.evStatus.reservChargeInfos.reservFlag",  # noqa
            )
            == 0
        ):
            vehicle.ev_schedule_charge_enabled = False

        vehicle.washer_fluid_warning_is_on = get_child_value(
            state, "vehicleStatus.washerFluidStatus"
        )
        vehicle.brake_fluid_warning_is_on = get_child_value(
            state, "vehicleStatus.breakOilStatus"
        )
        vehicle.fuel_level = get_child_value(state, "vehicleStatus.fuelLevel")
        vehicle.fuel_level_is_low = get_child_value(state, "vehicleStatus.lowFuelLight")
        vehicle.air_control_is_on = get_child_value(state, "vehicleStatus.airCtrlOn")
        vehicle.smart_key_battery_warning_is_on = get_child_value(
            state, "vehicleStatus.smartKeyBatteryWarning"
        )

        if get_child_value(state, "vehicleLocation.coord.lat"):
            vehicle.location = (
                get_child_value(state, "vehicleLocation.coord.lat"),
                get_child_value(state, "vehicleLocation.coord.lon"),
                parse_datetime(
                    get_child_value(state, "vehicleLocation.time"), self.data_timezone
                ),
            )
        vehicle.data = state

    def _update_vehicle_drive_info(self, vehicle: Vehicle, state: dict) -> None:
        vehicle.total_power_consumed = get_child_value(state, "totalPwrCsp")
        vehicle.total_power_regenerated = get_child_value(state, "regenPwr")
        vehicle.power_consumption_30d = get_child_value(state, "consumption30d")
        vehicle.daily_stats = get_child_value(state, "dailyStats")

    def _get_cached_vehicle_state(self, token: Token, vehicle: Vehicle) -> dict:
        url = self.SPA_API_URL + "vehicles/" + vehicle.id
        if vehicle.ccu_ccs2_protocol_support == 0:
            url = url + "/status/latest"
        else:
            url = url + "/ccs2/carstatus/latest"
        response = requests.get(
            url,
            headers=self._get_authenticated_headers(
                token, vehicle.ccu_ccs2_protocol_support
            ),
        ).json()
        _LOGGER.debug(f"{DOMAIN} - get_cached_vehicle_status response: {response}")
        _check_response_for_errors(response)
        if vehicle.ccu_ccs2_protocol_support == 0:
            response = response["resMsg"]["vehicleStatusInfo"]
        else:
            response = response["resMsg"]["state"]["Vehicle"]
        return response

    def _get_location(self, token: Token, vehicle: Vehicle) -> dict:
        url = self.SPA_API_URL + "vehicles/" + vehicle.id + "/location"

        try:
            response = requests.get(
                url,
                headers=self._get_authenticated_headers(
                    token, vehicle.ccu_ccs2_protocol_support
                ),
            ).json()
            _LOGGER.debug(f"{DOMAIN} - _get_location response: {response}")
            _check_response_for_errors(response)
            return response["resMsg"]["gpsDetail"]
        except Exception:
            _LOGGER.warning(f"{DOMAIN} - _get_location failed")
            return None

    def _get_forced_vehicle_state(self, token: Token, vehicle: Vehicle) -> dict:
        url = self.SPA_API_URL + "vehicles/" + vehicle.id + "/status"
        response = requests.get(
            url,
            headers=self._get_authenticated_headers(
                token, vehicle.ccu_ccs2_protocol_support
            ),
        ).json()
        _LOGGER.debug(f"{DOMAIN} - Received forced vehicle data: {response}")
        _check_response_for_errors(response)
        mapped_response = {}
        mapped_response["vehicleStatus"] = response["resMsg"]
        return mapped_response

    def lock_action(
        self, token: Token, vehicle: Vehicle, action: VEHICLE_LOCK_ACTION
    ) -> str:
        if not vehicle.ccu_ccs2_protocol_support:
            url = self.SPA_API_URL + "vehicles/" + vehicle.id + "/control/door"

            payload = {"action": action.value, "deviceId": token.device_id}
            headers = self._get_authenticated_headers(
                token, vehicle.ccu_ccs2_protocol_support
            )

        else:
            url = self.SPA_API_URL_V2 + "vehicles/" + vehicle.id + "/ccs2/control/door"

            payload = {"command": action.value}
            headers = self._get_control_headers(token, vehicle)

        _LOGGER.debug(f"{DOMAIN} - Lock Action Request: {payload}")

        response = requests.post(url, json=payload, headers=headers).json()
        _LOGGER.debug(f"{DOMAIN} - Lock Action Response: {response}")
        _check_response_for_errors(response)
        token.device_id = self._get_device_id(self._get_stamp())
        return response["msgId"]

    def charge_port_action(
        self, token: Token, vehicle: Vehicle, action: CHARGE_PORT_ACTION
    ) -> str:
        url = self.SPA_API_URL_V2 + "vehicles/" + vehicle.id + "/control/portdoor"

        payload = {"action": action.value}
        _LOGGER.debug(f"{DOMAIN} - Charge Port Action Request: {payload}")
        response = requests.post(
            url, json=payload, headers=self._get_control_headers(token, vehicle)
        ).json()
        _LOGGER.debug(f"{DOMAIN} - Charge Port Action Response: {response}")
        _check_response_for_errors(response)
        token.device_id = self._get_device_id(self._get_stamp())
        return response["msgId"]

    def start_climate(
        self, token: Token, vehicle: Vehicle, options: ClimateRequestOptions
    ) -> str:
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
        response = requests.post(
            url,
            json=payload,
            headers=self._get_authenticated_headers(
                token, vehicle.ccu_ccs2_protocol_support
            ),
        ).json()
        _LOGGER.debug(f"{DOMAIN} - Start Climate Action Response: {response}")
        _check_response_for_errors(response)
        token.device_id = self._get_device_id(self._get_stamp())
        return response["msgId"]

    def stop_climate(self, token: Token, vehicle: Vehicle) -> str:
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
        response = requests.post(
            url,
            json=payload,
            headers=self._get_authenticated_headers(
                token, vehicle.ccu_ccs2_protocol_support
            ),
        ).json()
        _LOGGER.debug(f"{DOMAIN} - Stop Climate Action Response: {response}")
        _check_response_for_errors(response)
        token.device_id = self._get_device_id(self._get_stamp())
        return response["msgId"]

    def start_hazard_lights(self, token: Token, vehicle: Vehicle) -> str:
        url = self.SPA_API_URL_V2 + "vehicles/" + vehicle.id + "/ccs2/control/light"

        payload = {"command": "on"}
        _LOGGER.debug(f"{DOMAIN} - Start Hazard Lights Request: {payload}")
        response = requests.post(
            url,
            json=payload,
            headers=self._get_control_headers(token, vehicle),
        ).json()
        _LOGGER.debug(f"{DOMAIN} - Start Hazard Lights Response: {response}")
        _check_response_for_errors(response)
        token.device_id = self._get_device_id(self._get_stamp())
        return response["msgId"]

    def start_hazard_lights_and_horn(self, token: Token, vehicle: Vehicle) -> str:
        url = self.SPA_API_URL_V2 + "vehicles/" + vehicle.id + "/ccs2/control/hornlight"

        payload = {"command": "on"}
        _LOGGER.debug(f"{DOMAIN} - Start Hazard Lights and Horn Request: {payload}")
        response = requests.post(
            url,
            json=payload,
            headers=self._get_control_headers(token, vehicle),
        ).json()
        _LOGGER.debug(f"{DOMAIN} - Start Hazard Lights and Horn Response: {response}")
        _check_response_for_errors(response)
        token.device_id = self._get_device_id(self._get_stamp())
        return response["msgId"]

    def _get_charge_limits(self, token: Token, vehicle: Vehicle) -> dict:
        # Not currently used as value is in the general get.
        # Most likely this forces the car the update it.
        url = f"{self.SPA_API_URL}vehicles/{vehicle.id}/charge/target"

        _LOGGER.debug(f"{DOMAIN} - Get Charging Limits Request")
        response = requests.get(
            url,
            headers=self._get_authenticated_headers(
                token, vehicle.ccu_ccs2_protocol_support
            ),
        ).json()
        _LOGGER.debug(f"{DOMAIN} - Get Charging Limits Response: {response}")
        _check_response_for_errors(response)
        # API sometimes returns multiple entries per plug type and they conflict.
        # The car itself says the last entry per plug type is the truth when tested
        # (EU Ioniq Electric Facelift MY 2019)
        if response["resMsg"] is not None:
            return response["resMsg"]

    def _get_trip_info(
        self,
        token: Token,
        vehicle: Vehicle,
        date_string: str,
        trip_period_type: int,
    ) -> dict:
        url = self.SPA_API_URL + "vehicles/" + vehicle.id + "/tripinfo"
        if trip_period_type == 0:  # month
            payload = {"tripPeriodType": 0, "setTripMonth": date_string}
        else:
            payload = {"tripPeriodType": 1, "setTripDay": date_string}

        _LOGGER.debug(f"{DOMAIN} - get_trip_info Request {payload}")
        response = requests.post(
            url,
            json=payload,
            headers=self._get_authenticated_headers(
                token, vehicle.ccu_ccs2_protocol_support
            ),
        )
        response = response.json()
        _LOGGER.debug(f"{DOMAIN} - get_trip_info response {response}")
        _check_response_for_errors(response)
        return response

    def update_month_trip_info(
        self,
        token,
        vehicle,
        yyyymm_string,
    ) -> None:
        """
        feature only available for some regions.
        Updates the vehicle.month_trip_info for the specified month.

        Default this information is None:

        month_trip_info: MonthTripInfo = None
        """
        vehicle.month_trip_info = None
        json_result = self._get_trip_info(
            token,
            vehicle,
            yyyymm_string,
            0,  # month trip info
        )
        msg = json_result["resMsg"]
        if msg["monthTripDayCnt"] > 0:
            result = MonthTripInfo(
                yyyymm=yyyymm_string,
                day_list=[],
                summary=TripInfo(
                    drive_time=msg["tripDrvTime"],
                    idle_time=msg["tripIdleTime"],
                    distance=msg["tripDist"],
                    avg_speed=msg["tripAvgSpeed"],
                    max_speed=msg["tripMaxSpeed"],
                ),
            )

            for day in msg["tripDayList"]:
                processed_day = DayTripCounts(
                    yyyymmdd=day["tripDayInMonth"],
                    trip_count=day["tripCntDay"],
                )
                result.day_list.append(processed_day)

            vehicle.month_trip_info = result

    def update_day_trip_info(
        self,
        token,
        vehicle,
        yyyymmdd_string,
    ) -> None:
        """
        feature only available for some regions.
        Updates the vehicle.day_trip_info information for the specified day.

        Default this information is None:

        day_trip_info: DayTripInfo = None
        """
        vehicle.day_trip_info = None
        json_result = self._get_trip_info(
            token,
            vehicle,
            yyyymmdd_string,
            1,  # day trip info
        )
        day_trip_list = json_result["resMsg"]["dayTripList"]
        if len(day_trip_list) > 0:
            msg = day_trip_list[0]
            result = DayTripInfo(
                yyyymmdd=yyyymmdd_string,
                trip_list=[],
                summary=TripInfo(
                    drive_time=msg["tripDrvTime"],
                    idle_time=msg["tripIdleTime"],
                    distance=msg["tripDist"],
                    avg_speed=msg["tripAvgSpeed"],
                    max_speed=msg["tripMaxSpeed"],
                ),
            )
            for trip in msg["tripList"]:
                processed_trip = TripInfo(
                    hhmmss=trip["tripTime"],
                    drive_time=trip["tripDrvTime"],
                    idle_time=trip["tripIdleTime"],
                    distance=trip["tripDist"],
                    avg_speed=trip["tripAvgSpeed"],
                    max_speed=trip["tripMaxSpeed"],
                )
                result.trip_list.append(processed_trip)

            vehicle.day_trip_info = result

    def _get_driving_info(self, token: Token, vehicle: Vehicle) -> dict:
        url = self.SPA_API_URL + "vehicles/" + vehicle.id + "/drvhistory"

        responseAlltime = requests.post(
            url,
            json={"periodTarget": 1},
            headers=self._get_authenticated_headers(
                token, vehicle.ccu_ccs2_protocol_support
            ),
        )
        responseAlltime = responseAlltime.json()
        _LOGGER.debug(f"{DOMAIN} - get_driving_info responseAlltime {responseAlltime}")
        _check_response_for_errors(responseAlltime)

        response30d = requests.post(
            url,
            json={"periodTarget": 0},
            headers=self._get_authenticated_headers(
                token, vehicle.ccu_ccs2_protocol_support
            ),
        )
        response30d = response30d.json()
        _LOGGER.debug(f"{DOMAIN} - get_driving_info response30d {response30d}")
        _check_response_for_errors(response30d)
        if get_child_value(responseAlltime, "resMsg.drivingInfo.0"):
            drivingInfo = responseAlltime["resMsg"]["drivingInfo"][0]

            drivingInfo["dailyStats"] = []
            if get_child_value(response30d, "resMsg.drivingInfoDetail.0"):
                for day in response30d["resMsg"]["drivingInfoDetail"]:
                    processedDay = DailyDrivingStats(
                        date=dt.datetime.strptime(day["drivingDate"], "%Y%m%d"),
                        total_consumed=get_child_value(day, "totalPwrCsp"),
                        engine_consumption=get_child_value(day, "motorPwrCsp"),
                        climate_consumption=get_child_value(day, "climatePwrCsp"),
                        onboard_electronics_consumption=get_child_value(
                            day, "eDPwrCsp"
                        ),
                        battery_care_consumption=get_child_value(
                            day, "batteryMgPwrCsp"
                        ),
                        regenerated_energy=get_child_value(day, "regenPwr"),
                        distance=get_child_value(day, "calculativeOdo"),
                        distance_unit=vehicle.odometer_unit,
                    )
                    drivingInfo["dailyStats"].append(processedDay)

            for drivingInfoItem in response30d["resMsg"]["drivingInfo"]:
                if (
                    drivingInfoItem["drivingPeriod"] == 0
                    and next(
                        (
                            v
                            for k, v in drivingInfoItem.items()
                            if k.lower() == "calculativeodo"
                        ),
                        0,
                    )
                    > 0
                ):
                    drivingInfo["consumption30d"] = round(
                        drivingInfoItem["totalPwrCsp"]
                        / drivingInfoItem["calculativeOdo"]
                    )
                    break

            return drivingInfo
        else:
            _LOGGER.debug(
                f"{DOMAIN} - Driving info didn't return valid data. This may be normal if the car doesn't support it."  # noqa
            )
            return None

    def set_charge_limits(
        self, token: Token, vehicle: Vehicle, ac: int, dc: int
    ) -> str:
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
        response = requests.post(
            url,
            json=body,
            headers=self._get_authenticated_headers(
                token, vehicle.ccu_ccs2_protocol_support
            ),
        ).json()
        _LOGGER.debug(f"{DOMAIN} - Set Charge Limits Response: {response}")
        _check_response_for_errors(response)
        return response["msgId"]

    def schedule_charging_and_climate(
        self,
        token: Token,
        vehicle: Vehicle,
        options: ScheduleChargingClimateRequestOptions,
    ) -> str:
        url = self.SPA_API_URL_V2 + "vehicles/" + vehicle.id
        url = url + "/ccs2"  # does not depend on vehicle.ccu_ccs2_protocol_support
        url = url + "/reservation/chargehvac"

        def set_default_departure_options(
            departure_options: ScheduleChargingClimateRequestOptions.DepartureOptions,
        ) -> None:
            if departure_options.enabled is None:
                departure_options.enabled = False
            if departure_options.days is None:
                departure_options.days = [0]
            if departure_options.time is None:
                departure_options.time = dt.time()

        if options.first_departure is None:
            options.first_departure = (
                ScheduleChargingClimateRequestOptions.DepartureOptions()
            )
        if options.second_departure is None:
            options.second_departure = (
                ScheduleChargingClimateRequestOptions.DepartureOptions()
            )

        set_default_departure_options(options.first_departure)
        set_default_departure_options(options.second_departure)
        departures = [options.first_departure, options.second_departure]

        if options.charging_enabled is None:
            options.charging_enabled = False
        if options.off_peak_start_time is None:
            options.off_peak_start_time = dt.time()
        if options.off_peak_end_time is None:
            options.off_peak_end_time = options.off_peak_start_time
        if options.off_peak_charge_only_enabled is None:
            options.off_peak_charge_only_enabled = False
        if options.climate_enabled is None:
            options.climate_enabled = False
        if options.temperature is None:
            options.temperature = 21.0
        if options.temperature_unit is None:
            options.temperature_unit = 0
        if options.defrost is None:
            options.defrost = False

        temperature: float = options.temperature
        if options.temperature_unit == 0:
            # Round to nearest 0.5
            temperature = round(temperature * 2.0) / 2.0
            # Cap at 27, floor at 17
            if temperature > 27.0:
                temperature = 27.0
            elif temperature < 17.0:
                temperature = 17.0

        payload = {
            "reservChargeInfo" + str(i + 1): {
                "reservChargeSet": departures[i].enabled,
                "reservInfo": {
                    "day": departures[i].days,
                    "time": {
                        "time": departures[i].time.strftime("%I%M"),
                        "timeSection": 1 if departures[i].time >= dt.time(12, 0) else 0,
                    },
                },
                "reservFatcSet": {
                    "airCtrl": 1 if options.climate_enabled else 0,
                    "airTemp": {
                        "value": f"{temperature:.1f}",
                        "hvacTempType": 1,
                        "unit": options.temperature_unit,
                    },
                    "heating1": 0,
                    "defrost": options.defrost,
                },
            }
            for i in range(2)
        }

        payload = payload | {
            "offPeakPowerInfo": {
                "offPeakPowerTime1": {
                    "endtime": {
                        "timeSection": (
                            1 if options.off_peak_end_time >= dt.time(12, 0) else 0
                        ),
                        "time": options.off_peak_end_time.strftime("%I%M"),
                    },
                    "starttime": {
                        "timeSection": (
                            1 if options.off_peak_start_time >= dt.time(12, 0) else 0
                        ),
                        "time": options.off_peak_start_time.strftime("%I%M"),
                    },
                },
                "offPeakPowerFlag": 2 if options.off_peak_charge_only_enabled else 1,
            },
            "reservFlag": 1 if options.charging_enabled else 0,
        }

        _LOGGER.debug(f"{DOMAIN} - Schedule Charging and Climate Request: {payload}")
        response = requests.post(
            url, json=payload, headers=self._get_control_headers(token, vehicle)
        ).json()
        _LOGGER.debug(f"{DOMAIN} - Schedule Charging and Climate Response: {response}")
        _check_response_for_errors(response)
        token.device_id = self._get_device_id(self._get_stamp())
        return response["msgId"]

    def valet_mode_action(
        self, token: Token, vehicle: Vehicle, action: VALET_MODE_ACTION
    ) -> str:
        url = self.SPA_API_URL_V2 + "vehicles/" + vehicle.id + "/control/valet"

        payload = {"action": action.value}
        _LOGGER.debug(f"{DOMAIN} - Valet Mode Action Request: {payload}")
        response = requests.post(
            url, json=payload, headers=self._get_control_headers(token, vehicle)
        ).json()
        _LOGGER.debug(f"{DOMAIN} - Valet Mode Action Response: {response}")
        _check_response_for_errors(response)
        token.device_id = self._get_device_id(self._get_stamp())
        return response["msgId"]

    def _get_stamp(self) -> str:
        raw_data = f"{self.APP_ID}:{int(dt.datetime.now().timestamp())}".encode()
        result = bytes(b1 ^ b2 for b1, b2 in zip(self.CFB, raw_data))
        return base64.b64encode(result).decode("utf-8")

    def _get_device_id(self, stamp: str):
        my_hex = "%064x" % random.randrange(  # pylint: disable=consider-using-f-string
            10**80
        )
        registration_id = my_hex[:64]
        url = self.SPA_API_URL + "notifications/register"
        payload = {
            "pushRegId": registration_id,
            "pushType": self.PUSH_TYPE,
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

        _LOGGER.debug(f"{DOMAIN} - Get Device ID request: {url} {headers} {payload}")
        response = requests.post(url, headers=headers, json=payload)
        response = response.json()
        _check_response_for_errors(response)
        _LOGGER.debug(f"{DOMAIN} - Get Device ID response: {response}")

        device_id = response["resMsg"]["deviceId"]
        return device_id

    def _get_cookies(self) -> dict:
        # Get Cookies #
        url = (
            self.USER_API_URL
            + "oauth2/authorize?response_type=code&state=test&client_id="
            + self.CLIENT_ID
            + "&redirect_uri="
            + self.USER_API_URL
            + "oauth2/redirect&lang="
            + self.LANGUAGE
        )

        _LOGGER.debug(f"{DOMAIN} - Get cookies request: {url}")
        session = requests.Session()
        _ = session.get(url)
        _LOGGER.debug(f"{DOMAIN} - Get cookies response: {session.cookies.get_dict()}")
        return session.cookies.get_dict()
        # return session

    def _set_session_language(self, cookies) -> None:
        # Set Language for Session #
        url = self.USER_API_URL + "language"
        headers = {"Content-type": "application/json"}
        payload = {"lang": self.LANGUAGE}
        _ = requests.post(url, json=payload, headers=headers, cookies=cookies)

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
            f"{DOMAIN} - LoginFormSubmit {login_form_action_url} - Response {response.status_code} - {response.headers}"  # noqa
        )
        if response.status_code != 302:
            _LOGGER.debug(
                f"{DOMAIN} - LoginFormSubmit Error {login_form_action_url} - Response {response.status_code} - {response.text}"  # noqa
            )
            return

        redirect_url = response.headers["Location"]
        headers = {"User-Agent": USER_AGENT_MOZILLA}
        response = requests.get(redirect_url, headers=headers, cookies=cookies)
        cookies = cookies | response.cookies.get_dict()
        _LOGGER.debug(
            f"{DOMAIN} - Redirect User Id {redirect_url} - Response {response.url} - {response.text}"  # noqa
        )

        if "account-find-link" in response.text:
            soup = BeautifulSoup(response.content, "html.parser")
            login_form_action_url = soup.find("form")["action"].replace("&amp;", "&")
            data = {"actionType": "FIND", "createToUVO": "UVO", "email": ""}
            headers = {
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": USER_AGENT_MOZILLA,
                "followRedirects": "false",
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
                    f"{DOMAIN} - AccountFindLink Error {login_form_action_url} - Response {response.status_code}"  # noqa
                )
                return

            cookies = cookies | response.cookies.get_dict()

        url = self.USER_API_URL + "silentsignin"
        headers = {
            "User-Agent": USER_AGENT_MOZILLA,
            "ccsp-service-id": self.CCSP_SERVICE_ID,
        }
        response = requests.post(
            url,
            headers=headers,
            json={"intUserId": "0"},
            cookies=cookies,
        ).json()
        _LOGGER.debug(f"{DOMAIN} - silentsignin Response {response}")
        parsed_url = urlparse(response["redirectUrl"])
        authorization_code = "".join(parse_qs(parsed_url.query)["code"])
        return authorization_code

    def _get_access_token(self, stamp, authorization_code):
        # Get Access Token #
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
        # Get Refresh Token #
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
            "grant_type=refresh_token&redirect_uri=https%3A%2F%2Fwww.getpostman.com%2Foauth2%2Fcallback&refresh_token="  # noqa
            + authorization_code
        )
        _LOGGER.debug(f"{DOMAIN} - Get Refresh Token Data: {data}")
        response = requests.post(url, data=data, headers=headers)
        response = response.json()
        _LOGGER.debug(f"{DOMAIN} - Get Refresh Token Response: {response}")
        token_type = response["token_type"]
        refresh_token = token_type + " " + response["access_token"]
        return token_type, refresh_token

    def _get_control_token(self, token: Token) -> Token:
        url = self.USER_API_URL + "pin?token="
        headers = {
            "Authorization": token.access_token,
            "Content-type": "application/json",
            "Host": self.BASE_URL,
            "Accept-Encoding": "gzip",
            "User-Agent": USER_AGENT_OK_HTTP,
        }

        data = {"deviceId": token.device_id, "pin": token.pin}
        _LOGGER.debug(f"{DOMAIN} - Get Control Token Data: {data}")
        response = requests.put(url, json=data, headers=headers)
        response = response.json()
        _LOGGER.debug(f"{DOMAIN} - Get Control Token Response {response}")
        control_token = "Bearer " + response["controlToken"]
        control_token_expire_at = math.floor(
            dt.datetime.now().timestamp() + response["expiresTime"]
        )
        return control_token, control_token_expire_at

    def check_action_status(
        self,
        token: Token,
        vehicle: Vehicle,
        action_id: str,
        synchronous: bool = False,
        timeout: int = 0,
    ) -> OrderStatus:
        url = self.SPA_API_URL + "notifications/" + vehicle.id + "/records"

        if synchronous:
            if timeout < 1:
                raise APIError("Timeout must be 1 or higher")

            end_time = dt.datetime.now() + dt.timedelta(seconds=timeout)
            while end_time > dt.datetime.now():
                # recursive call with Synchronous set to False
                state = self.check_action_status(
                    token, vehicle, action_id, synchronous=False
                )
                if state == OrderStatus.PENDING:
                    # state pending: recheck regularly
                    # (until we get a final state or exceed the timeout)
                    sleep(5)
                else:
                    # any other state is final
                    return state

            # if we exit the loop after the set timeout, return a Timeout state
            return OrderStatus.TIMEOUT

        else:
            response = requests.get(
                url,
                headers=self._get_authenticated_headers(
                    token, vehicle.ccu_ccs2_protocol_support
                ),
            ).json()
            _LOGGER.debug(f"{DOMAIN} - Check last action status Response: {response}")
            _check_response_for_errors(response)

            for action in response["resMsg"]:
                if action["recordId"] == action_id:
                    if action["result"] == "success":
                        return OrderStatus.SUCCESS
                    elif action["result"] == "fail":
                        return OrderStatus.FAILED
                    elif action["result"] == "non-response":
                        return OrderStatus.TIMEOUT
                    elif action["result"] is None:
                        _LOGGER.info(
                            "Action status not set yet by server - try again in a few seconds"  # noqa
                        )
                        return OrderStatus.PENDING

            # if iterate the whole notifications list and
            # can't find the action, raise an exception
            # Old code: raise APIError(f"No action found with ID {action_id}")
            return OrderStatus.UNKNOWN
