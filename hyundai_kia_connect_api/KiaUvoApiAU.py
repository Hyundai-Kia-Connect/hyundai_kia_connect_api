"""KiaUvoApiAU"""

# pylint:disable=missing-timeout,missing-class-docstring,missing-function-docstring,wildcard-import,unused-wildcard-import,invalid-name,logging-fstring-interpolation,broad-except,bare-except,super-init-not-called,unused-argument,line-too-long,too-many-lines

import base64
import datetime as dt
import math
import logging
import random
import uuid
from time import sleep
from urllib.parse import parse_qs, urlparse

import pytz
import requests
from dateutil import tz

from .ApiImpl import (
    ClimateRequestOptions,
    WindowRequestOptions,
)
from .ApiImplType1 import ApiImplType1
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
    OrderStatus,
)
from .exceptions import (
    AuthenticationError,
    DuplicateRequestError,
    RequestTimeoutError,
    ServiceTemporaryUnavailable,
    NoDataFound,
    InvalidAPIResponseError,
    APIError,
    RateLimitingError,
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


def _check_response_for_errors(response: dict) -> None:
    """
    Checks for errors in the API response.
    If an error is found, an exception is raised.
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
    - 9999: "Undefined Error - Response timeout"
    :param response: the API's JSON response
    """

    error_code_mapping = {
        "4004": DuplicateRequestError,
        "4081": RequestTimeoutError,
        "5031": ServiceTemporaryUnavailable,
        "5091": RateLimitingError,
        "5921": NoDataFound,
        "9999": RequestTimeoutError,
    }

    if not any(x in response for x in ["retCode", "resCode", "resMsg"]):
        _LOGGER.error(f"Unknown API response format: {response}")
        raise InvalidAPIResponseError()

    if response["retCode"] == "F":
        if response["resCode"] in error_code_mapping:
            raise error_code_mapping[response["resCode"]](response["resMsg"])
        else:
            raise APIError(f"Server returned: '{response['resMsg']}'")


class KiaUvoApiAU(ApiImplType1):
    data_timezone = tz.gettz("Australia/Sydney")
    temperature_range = [x * 0.5 for x in range(34, 54)]

    def __init__(self, region: int, brand: int, language: str) -> None:
        self.brand = brand
        if BRANDS[brand] == BRAND_KIA:
            self.BASE_URL: str = "au-apigw.ccs.kia.com.au:8082"
            self.CCSP_SERVICE_ID: str = "8acb778a-b918-4a8d-8624-73a0beb64289"
            self.APP_ID: str = "4ad4dcde-be23-48a8-bc1c-91b94f5c06f8"  # Android app ID
            self.BASIC_AUTHORIZATION: str = "Basic OGFjYjc3OGEtYjkxOC00YThkLTg2MjQtNzNhMGJlYjY0Mjg5OjdTY01NbTZmRVlYZGlFUEN4YVBhUW1nZVlkbFVyZndvaDRBZlhHT3pZSVMyQ3U5VA=="
        elif BRANDS[brand] == BRAND_HYUNDAI:
            self.BASE_URL: str = "au-apigw.ccs.hyundai.com.au:8080"
            self.CCSP_SERVICE_ID: str = "855c72df-dfd7-4230-ab03-67cbf902bb1c"
            self.APP_ID: str = "f9ccfdac-a48d-4c57-bd32-9116963c24ed"  # Android app ID
            self.BASIC_AUTHORIZATION: str = "Basic ODU1YzcyZGYtZGZkNy00MjMwLWFiMDMtNjdjYmY5MDJiYjFjOmU2ZmJ3SE0zMllOYmhRbDBwdmlhUHAzcmY0dDNTNms5MWVjZUEzTUpMZGJkVGhDTw=="

        self.USER_API_URL: str = "https://" + self.BASE_URL + "/api/v1/user/"
        self.SPA_API_URL: str = "https://" + self.BASE_URL + "/api/v1/spa/"
        self.SPA_API_URL_V2: str = "https://" + self.BASE_URL + "/api/v2/spa/"
        self.CLIENT_ID: str = self.CCSP_SERVICE_ID

    def _get_control_headers(self, token: Token) -> dict:
        control_token, _ = self._get_control_token(token)
        authenticated_headers = self._get_authenticated_headers(token)
        return authenticated_headers | {
            "Authorization": control_token,
            "AuthorizationCCSP": control_token,
        }

    def login(self, username: str, password: str) -> Token:
        stamp = self._get_stamp()
        device_id = self._get_device_id(stamp)
        cookies = self._get_cookies()
        # self._set_session_language(cookies)
        authorization_code = None
        try:
            authorization_code = self._get_authorization_code_with_redirect_url(
                username, password, cookies
            )
        except Exception:
            _LOGGER.debug(f"{DOMAIN} - get_authorization_code_with_redirect_url failed")

        if authorization_code is None:
            raise AuthenticationError("Login Failed")

        _, access_token, authorization_code = self._get_access_token(
            authorization_code, stamp
        )
        _, refresh_token = self._get_refresh_token(authorization_code, stamp)
        valid_until = dt.datetime.now(pytz.utc) + dt.timedelta(hours=23)

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
            url, headers=self._get_authenticated_headers(token)
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

        if is_ccs2:
            state = response["resMsg"]["state"]["Vehicle"]
            self._update_vehicle_properties_ccs2(vehicle, state)
        else:
            location = self._get_location(token, vehicle)
            self._update_vehicle_properties(
                vehicle,
                {
                    "status": response["resMsg"],
                    "vehicleLocation": location,
                },
            )

        if vehicle.engine_type == ENGINE_TYPES.EV:
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
        status = self._get_forced_vehicle_state(token, vehicle)
        location = self._get_location(token, vehicle)
        self._update_vehicle_properties(
            vehicle,
            {
                "status": status,
                "vehicleLocation": location,
            },
        )
        # Only call for driving info on cars we know have a chance of supporting it.
        # Could be expanded if other types do support it.
        if vehicle.engine_type == ENGINE_TYPES.EV:
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
        if get_child_value(state, "status.time"):
            vehicle.last_updated_at = parse_datetime(
                get_child_value(state, "status.time"), self.data_timezone
            )
        else:
            vehicle.last_updated_at = dt.datetime.now(self.data_timezone)

        vehicle.odometer = (
            get_child_value(state, "status.odometer.value"),
            DISTANCE_UNITS[
                get_child_value(
                    state,
                    "status.odometer.unit",
                )
            ],
        )
        vehicle.car_battery_percentage = get_child_value(state, "status.battery.batSoc")
        vehicle.engine_is_running = get_child_value(state, "status.engine")

        if get_child_value(state, "status.airTemp.value"):
            tempIndex = get_hex_temp_into_index(
                get_child_value(state, "status.airTemp.value")
            )

            vehicle.air_temperature = (
                self.temperature_range[tempIndex],
                TEMPERATURE_UNITS[
                    get_child_value(
                        state,
                        "status.airTemp.unit",
                    )
                ],
            )
        vehicle.defrost_is_on = get_child_value(state, "status.defrost")
        steer_wheel_heat = get_child_value(state, "status.steerWheelHeat")
        if steer_wheel_heat in [0, 2]:
            vehicle.steering_wheel_heater_is_on = False
        elif steer_wheel_heat == 1:
            vehicle.steering_wheel_heater_is_on = True

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

        ev_charge_port_door_is_open = get_child_value(
            state, "status.evStatus.chargePortDoorOpenStatus"
        )

        if ev_charge_port_door_is_open == 1:
            vehicle.ev_charge_port_door_is_open = True
        elif ev_charge_port_door_is_open == 2:
            vehicle.ev_charge_port_door_is_open = False
        if (
            get_child_value(
                state,
                "status.evStatus.drvDistance.0.rangeByFuel.totalAvailableRange.value",  # noqa
            )
            is not None
        ):
            vehicle.total_driving_range = (
                round(
                    float(
                        get_child_value(
                            state,
                            "status.evStatus.drvDistance.0.rangeByFuel.totalAvailableRange.value",  # noqa
                        )
                    ),
                    1,
                ),
                DISTANCE_UNITS[
                    get_child_value(
                        state,
                        "status.evStatus.drvDistance.0.rangeByFuel.totalAvailableRange.unit",  # noqa
                    )
                ],
            )
        if (
            get_child_value(
                state,
                "status.evStatus.drvDistance.0.rangeByFuel.evModeRange.value",
            )
            is not None
        ):
            vehicle.ev_driving_range = (
                round(
                    float(
                        get_child_value(
                            state,
                            "status.evStatus.drvDistance.0.rangeByFuel.evModeRange.value",  # noqa
                        )
                    ),
                    1,
                ),
                DISTANCE_UNITS[
                    get_child_value(
                        state,
                        "status.evStatus.drvDistance.0.rangeByFuel.evModeRange.unit",  # noqa
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

        target_soc_list = get_child_value(
            state, "status.evStatus.reservChargeInfos.targetSOClist"
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
                "status.evStatus.drvDistance.0.rangeByFuel.gasModeRange.value",
            )
            is not None
        ):
            vehicle.fuel_driving_range = (
                get_child_value(
                    state,
                    "status.evStatus.drvDistance.0.rangeByFuel.gasModeRange.value",  # noqa
                ),
                DISTANCE_UNITS[
                    get_child_value(
                        state,
                        "status.evStatus.drvDistance.0.rangeByFuel.gasModeRange.unit",  # noqa
                    )
                ],
            )
        elif get_child_value(
            state,
            "status.dte.value",
        ):
            vehicle.fuel_driving_range = (
                get_child_value(
                    state,
                    "status.dte.value",
                ),
                DISTANCE_UNITS[get_child_value(state, "status.dte.unit")],
            )

        vehicle.ev_target_range_charge_AC = (
            get_child_value(
                state,
                "status.evStatus.reservChargeInfos.targetSOClist.1.dte.rangeByFuel.totalAvailableRange.value",  # noqa
            ),
            DISTANCE_UNITS[
                get_child_value(
                    state,
                    "status.evStatus.reservChargeInfos.targetSOClist.1.dte.rangeByFuel.totalAvailableRange.unit",  # noqa
                )
            ],
        )
        vehicle.ev_target_range_charge_DC = (
            get_child_value(
                state,
                "status.evStatus.reservChargeInfos.targetSOClist.0.dte.rangeByFuel.totalAvailableRange.value",  # noqa
            ),
            DISTANCE_UNITS[
                get_child_value(
                    state,
                    "status.evStatus.reservChargeInfos.targetSOClist.0.dte.rangeByFuel.totalAvailableRange.unit",  # noqa
                )
            ],
        )
        vehicle.ev_first_departure_enabled = get_child_value(
            state,
            "status.evStatus.reservChargeInfos.reservChargeInfo.reservChargeInfoDetail.reservChargeSet",  # noqa
        )
        vehicle.ev_second_departure_enabled = get_child_value(
            state,
            "status.evStatus.reservChargeInfos.reserveChargeInfo2.reservChargeInfoDetail.reservChargeSet",  # noqa
        )
        vehicle.ev_first_departure_days = get_child_value(
            state,
            "status.evStatus.reservChargeInfos.reservChargeInfo.reservChargeInfoDetail.reservInfo.day",  # noqa
        )
        vehicle.ev_second_departure_days = get_child_value(
            state,
            "status.evStatus.reservChargeInfos.reserveChargeInfo2.reservChargeInfoDetail.reservInfo.day",  # noqa
        )

        vehicle.ev_first_departure_time = self._get_time_from_string(
            get_child_value(
                state,
                "status.evStatus.reservChargeInfos.reservChargeInfo.reservChargeInfoDetail.reservInfo.time.time",  # noqa
            ),
            get_child_value(
                state,
                "status.evStatus.reservChargeInfos.reservChargeInfo.reservChargeInfoDetail.reservInfo.time.timeSection",  # noqa
            ),
        )

        vehicle.ev_second_departure_time = self._get_time_from_string(
            get_child_value(
                state,
                "status.evStatus.reservChargeInfos.reserveChargeInfo2.reservChargeInfoDetail.reservInfo.time.time",  # noqa
            ),
            get_child_value(
                state,
                "status.evStatus.reservChargeInfos.reserveChargeInfo2.reservChargeInfoDetail.reservInfo.time.timeSection",  # noqa
            ),
        )

        vehicle.ev_off_peak_start_time = self._get_time_from_string(
            get_child_value(
                state,
                "status.evStatus.reservChargeInfos.offpeakPowerInfo.offPeakPowerTime1.starttime.time",  # noqa
            ),
            get_child_value(
                state,
                "status.evStatus.reservChargeInfos.offpeakPowerInfo.offPeakPowerTime1.starttime.timeSection",  # noqa
            ),
        )

        vehicle.ev_off_peak_end_time = self._get_time_from_string(
            get_child_value(
                state,
                "status.evStatus.reservChargeInfos.offpeakPowerInfo.offPeakPowerTime1.endtime.time",  # noqa
            ),
            get_child_value(
                state,
                "status.evStatus.reservChargeInfos.offpeakPowerInfo.offPeakPowerTime1.endtime.timeSection",  # noqa
            ),
        )

        if get_child_value(
            state,
            "status.evStatus.reservChargeInfos.offpeakPowerInfo.offPeakPowerFlag",  # noqa
        ):
            if (
                get_child_value(
                    state,
                    "status.evStatus.reservChargeInfos.offpeakPowerInfo.offPeakPowerFlag",  # noqa
                )
                == 1
            ):
                vehicle.ev_off_peak_charge_only_enabled = True
            elif (
                get_child_value(
                    state,
                    "status.evStatus.reservChargeInfos.offpeakPowerInfo.offPeakPowerFlag",  # noqa
                )
                == 2
            ):
                vehicle.ev_off_peak_charge_only_enabled = False

        vehicle.washer_fluid_warning_is_on = get_child_value(
            state, "status.washerFluidStatus"
        )
        vehicle.brake_fluid_warning_is_on = get_child_value(
            state, "status.breakOilStatus"
        )
        vehicle.fuel_level = get_child_value(state, "status.fuelLevel")
        vehicle.fuel_level_is_low = get_child_value(state, "status.lowFuelLight")
        vehicle.air_control_is_on = get_child_value(state, "status.airCtrlOn")
        vehicle.smart_key_battery_warning_is_on = get_child_value(
            state, "status.smartKeyBatteryWarning"
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
        vehicle.power_consumption_30d = get_child_value(state, "consumption30d")
        vehicle.daily_stats = get_child_value(state, "dailyStats")

    def _get_location(self, token: Token, vehicle: Vehicle) -> dict:
        url = self.SPA_API_URL + "vehicles/" + vehicle.id + "/location/park"

        try:
            response = requests.get(
                url, headers=self._get_authenticated_headers(token)
            ).json()
            _LOGGER.debug(f"{DOMAIN} - _get_location response: {response}")
            _check_response_for_errors(response)
            return response["resMsg"]["gpsDetail"]
        except Exception:
            _LOGGER.debug(f"{DOMAIN} - _get_location failed")
            return None

    def _get_forced_vehicle_state(self, token: Token, vehicle: Vehicle) -> dict:
        url = self.SPA_API_URL + "vehicles/" + vehicle.id + "/status"
        response = requests.get(
            url, headers=self._get_authenticated_headers(token)
        ).json()
        _LOGGER.debug(f"{DOMAIN} - Received forced vehicle data: {response}")
        _check_response_for_errors(response)
        mapped_response = {}
        mapped_response["vehicleStatus"] = response["resMsg"]
        return mapped_response

    def lock_action(
        self, token: Token, vehicle: Vehicle, action: VEHICLE_LOCK_ACTION
    ) -> str:
        url = self.SPA_API_URL_V2 + "vehicles/" + vehicle.id + "/control/door"

        payload = {"action": action.value, "deviceId": token.device_id}
        _LOGGER.debug(f"{DOMAIN} - Lock Action Request: {payload}")
        response = requests.post(
            url, json=payload, headers=self._get_control_headers(token)
        ).json()
        _LOGGER.debug(f"{DOMAIN} - Lock Action Response: {response}")
        _check_response_for_errors(response)
        return response["msgId"]

    def set_windows_state(
        self, token: Token, vehicle: Vehicle, options: WindowRequestOptions
    ) -> str:
        url = self.SPA_API_URL_V2 + "vehicles/" + vehicle.id + "/control/windowcurtain"

        payload = {
            "backLeft": options.back_left,
            "backRight": options.back_right,
            "frontLeft": options.front_left,
            "frontRight": options.front_right,
        }
        _LOGGER.debug(f"{DOMAIN} - Window State Action Request: {payload}")
        response = requests.post(
            url, json=payload, headers=self._get_control_headers(token)
        ).json()
        _LOGGER.debug(f"{DOMAIN} - Window State Action Response: {response}")
        _check_response_for_errors(response)
        return response["msgId"]

    def charge_port_action(
        self, token: Token, vehicle: Vehicle, action: CHARGE_PORT_ACTION
    ) -> str:
        # TODO: needs verification
        url = self.SPA_API_URL_V2 + "vehicles/" + vehicle.id + "/control/portdoor"

        payload = {"action": action.value, "deviceId": token.device_id}
        _LOGGER.debug(f"{DOMAIN} - Charge Port Action Request: {payload}")
        response = requests.post(
            url, json=payload, headers=self._get_control_headers(token)
        ).json()
        _LOGGER.debug(f"{DOMAIN} - Charge Port Action Response: {response}")
        _check_response_for_errors(response)
        return response["msgId"]

    def start_climate(
        self, token: Token, vehicle: Vehicle, options: ClimateRequestOptions
    ) -> str:
        url = self.SPA_API_URL_V2 + "vehicles/" + vehicle.id + "/control/engine"

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
            "hvacType": 1,
            "options": {
                "defrost": options.defrost,
                "heating1": int(options.heating),
            },
            "tempCode": hex_set_temp,
            "unit": "C",
        }
        _LOGGER.debug(f"{DOMAIN} - Start Climate Action Request: {payload}")
        response = requests.post(
            url, json=payload, headers=self._get_control_headers(token)
        ).json()
        _LOGGER.debug(f"{DOMAIN} - Start Climate Action Response: {response}")
        _check_response_for_errors(response)
        return response["msgId"]

    def stop_climate(self, token: Token, vehicle: Vehicle) -> str:
        url = self.SPA_API_URL_V2 + "vehicles/" + vehicle.id + "/control/engine"
        payload = {
            "action": "stop",
        }
        _LOGGER.debug(f"{DOMAIN} - Stop Climate Action Request: {payload}")
        response = requests.post(
            url, json=payload, headers=self._get_control_headers(token)
        ).json()
        _LOGGER.debug(f"{DOMAIN} - Stop Climate Action Response: {response}")
        _check_response_for_errors(response)
        return response["msgId"]

    def start_charge(self, token: Token, vehicle: Vehicle) -> str:
        url = self.SPA_API_URL + "vehicles/" + vehicle.id + "/control/charge"

        payload = {"action": "start", "deviceId": token.device_id}
        _LOGGER.debug(f"{DOMAIN} - Start Charge Action Request: {payload}")
        response = requests.post(
            url, json=payload, headers=self._get_control_headers(token)
        ).json()
        _LOGGER.debug(f"{DOMAIN} - Start Charge Action Response: {response}")
        _check_response_for_errors(response)
        return response["msgId"]

    def stop_charge(self, token: Token, vehicle: Vehicle) -> str:
        url = self.SPA_API_URL + "vehicles/" + vehicle.id + "/control/charge"

        payload = {"action": "stop", "deviceId": token.device_id}
        _LOGGER.debug(f"{DOMAIN} - Stop Charge Action Request {payload}")
        response = requests.post(
            url, json=payload, headers=self._get_control_headers(token)
        ).json()
        _LOGGER.debug(f"{DOMAIN} - Stop Charge Action Response: {response}")
        _check_response_for_errors(response)
        return response["msgId"]

    def _get_charge_limits(self, token: Token, vehicle: Vehicle) -> dict:
        # Not currently used as value is in the general get.
        # Most likely this forces the car the update it.
        url = f"{self.SPA_API_URL}vehicles/{vehicle.id}/charge/target"

        _LOGGER.debug(f"{DOMAIN} - Get Charging Limits Request")
        response = requests.get(
            url, headers=self._get_authenticated_headers(token)
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
            headers=self._get_authenticated_headers(token),
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
        Europe feature only.
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
        Europe feature only.
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
            headers=self._get_authenticated_headers(token),
        )
        responseAlltime = responseAlltime.json()
        _LOGGER.debug(f"{DOMAIN} - get_driving_info responseAlltime {responseAlltime}")
        _check_response_for_errors(responseAlltime)

        response30d = requests.post(
            url,
            json={"periodTarget": 0},
            headers=self._get_authenticated_headers(token),
        )
        response30d = response30d.json()
        _LOGGER.debug(f"{DOMAIN} - get_driving_info response30d {response30d}")
        _check_response_for_errors(response30d)
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
                    distance=day["calculativeOdo"],
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
            url, json=body, headers=self._get_control_headers(token)
        ).json()
        _LOGGER.debug(f"{DOMAIN} - Set Charge Limits Response: {response}")
        _check_response_for_errors(response)
        return response["msgId"]

    def _get_stamp(self) -> str:
        if BRANDS[self.brand] == BRAND_KIA:
            cfb = base64.b64decode(
                "IDbMgWBXgic4MAyMgf5PFFRAdGX5O3IyC3uvN3scCs0gDpTFDuyvBorlAH9JMM2/wMc="
            )
        elif BRANDS[self.brand] == BRAND_HYUNDAI:
            cfb = base64.b64decode(
                "V60WkEmyRQaAfrBF1623/7QL62MjLVbCHdItGzQ1g5T/hkmKmMVTaMHv4cKGzgD3gL8="
            )
        else:
            raise ValueError("Invalid brand")
        raw_data = f"{self.APP_ID}:{int(dt.datetime.now().timestamp())}".encode()
        result = bytes(b1 ^ b2 for b1, b2 in zip(cfb, raw_data))
        return base64.b64encode(result).decode("utf-8")

    def _get_device_id(self, stamp):
        my_hex = "%064x" % random.randrange(  # pylint: disable=consider-using-f-string
            10**80
        )
        registration_id = my_hex[:64]
        # provider_device_id = "59af09e554a9442ab8589c9500d04d2e"
        url = self.SPA_API_URL + "notifications/register"
        payload = {
            # "providerDeviceId": provider_device_id,
            "pushRegId": registration_id,
            "pushType": "GCM",
            "uuid": str(uuid.uuid4()),
        }

        headers = {
            "ccsp-service-id": self.CLIENT_ID,
            "ccsp-application-id": self.APP_ID,
            "Stamp": stamp,
            "Content-Type": "application/json;charset=UTF-8",
            "Host": self.BASE_URL,
            "Connection": "Keep-Alive",
            "Accept-Encoding": "gzip",
            "User-Agent": USER_AGENT_OK_HTTP,
        }

        _LOGGER.debug(f"{DOMAIN} - Get Device ID request: {headers} {payload}")
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
            + "oauth2/authorize?response_type=code&client_id="
            + self.CLIENT_ID
            + "&redirect_uri="
            + "https://"
            + self.BASE_URL
            + "/api/v1/user/oauth2/redirect&lang=en"
        )

        _LOGGER.debug(f"{DOMAIN} - Get cookies request: {url}")
        session = requests.Session()
        _ = session.get(url)
        _LOGGER.debug(f"{DOMAIN} - Get cookies response: {session.cookies.get_dict()}")
        return session.cookies.get_dict()
        # return session

    def _set_session_language(self, cookies) -> None:
        # Set Language for Session #
        url = self.USER_API_URL
        headers = {"Content-type": "application/json"}
        payload = {"lang": "en"}
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

    def _get_access_token(self, authorization_code, stamp):
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
            + self.BASE_URL
            + "%2Fapi%2Fv1%2Fuser%2Foauth2%2Fredirect&code="
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

    def _get_refresh_token(self, authorization_code, stamp):
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

        data = "grant_type=refresh_token&refresh_token=" + authorization_code
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
                url, headers=self._get_authenticated_headers(token)
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
                        _LOGGER.debug(
                            "Action status not set yet by server - try again in a few seconds"  # noqa
                        )
                        return OrderStatus.PENDING

            # if iterate the whole notifications list and
            # can't find the action, raise an exception
            raise APIError(f"No action found with ID {action_id}")
