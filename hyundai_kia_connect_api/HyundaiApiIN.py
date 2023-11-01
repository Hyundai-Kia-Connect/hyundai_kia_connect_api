import base64
import uuid
import random
import requests
import logging
import pytz
import re
from dateutil import tz
import datetime as dt
from urllib.parse import parse_qs, urlparse

from .exceptions import *
from .ApiImpl import (
    ApiImpl,
    ClimateRequestOptions,
)
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
    BRAND_GENESIS,
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
from .utils import (
    get_child_value,
    get_index_into_hex_temp,
    get_hex_temp_into_index,
)


USER_AGENT_OK_HTTP: str = "okhttp/3.12.0"
_LOGGER = logging.getLogger(__name__)


def _check_response_for_errors(response: dict) -> None:
    """
    Checks for errors in the API response.
    If an error is found, an exception is raised.
    retCode known values:
    - S: success
    - F: failure
    resCode / resMsg known values:
    - 0000: no error
    - 4002:  "Invalid request body - invalid deviceId",
             relogin will resolve but a bandaid.
    - 4004: "Duplicate request"
    - 4081: "Request timeout"
    - 5031: "Unavailable remote control - Service Temporary Unavailable"
    - 5091: "Exceeds number of requests"
    - 5921: "No Data Found v2 - No Data Found v2"
    - 9999: "Undefined Error - Response timeout"
    :param response: the API's JSON response
    """

    error_code_mapping = {
        "4002": DeviceIDError,
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
        raise APIError(
            f"Server returned:  '{response['rescode']}' '{response['resMsg']}'"
        )



class HyundaiApiIN(ApiImpl):
    data_timezone = tz.gettz("Asia/Kolkata")
    temperature_range = [x * 0.5 for x in range(28, 60)]

    def __init__(self, brand: int) -> None:
        super().__init__()
        self.brand = brand
        if BRANDS[brand] == BRAND_HYUNDAI:
            self.BASE_DOMAIN: str = "prd.in-ccapi.hyundai.connected-car.io"
            self.PORT: int = 8080
            self.CCSP_SERVICE_ID: str = "e5b3f6d0-7f83-43c9-aff3-a254db7af368"
            self.APP_ID: str = "5a27df80-4ca1-4154-8c09-6f4029d91cf7"
            self.BASIC_AUTHORIZATION: str = "Basic ZTViM2Y2ZDAtN2Y4My00M2M5LWFmZjMtYTI1NGRiN2FmMzY4OjVKRk9DcjZDMjRPZk96bERxWnA3RXdxcmtMMFd3MDRVYXhjRGlFNlVkM3FJNVNFNA=="  # noqa
            self.LOGIN_FORM_HOST = "prd.in-ccapi.hyundai.connected-car.io"
            self.PUSH_TYPE = "GCM"
            self.GCM_SENDER_ID = 974204007939
            self.CLIENT_ID = self.CCSP_SERVICE_ID
            # This is not sure
            self.CFB: str = base64.b64decode(
                "RFtoRq/vDXJmRndoZaZQyfOot7OrIqGVFj96iY2WL3yyH5Z/pUvlUhqmCxD2t+D65SQ="
            )
        elif BRANDS[brand] == BRAND_KIA:
            raise NotImplemented()

        self.BASE_URL: str = self.BASE_DOMAIN + ":" + str(self.PORT)
        self.USER_API_URL: str = "https://" + self.BASE_URL + "/api/v1/user/"
        self.SPA_API_URL: str = "https://" + self.BASE_URL + "/api/v1/spa/"
        self.SPA_API_URL_V2: str = "https://" + self.BASE_URL + "/api/v2/spa/"

    def _get_authenticated_headers(self, token: Token) -> dict:
        return {
            "Authorization": token.access_token,
            "ccsp-service-id": self.CCSP_SERVICE_ID,
            "ccsp-application-id": self.APP_ID,
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
            stamp, authorization_code
        )
        _, refresh_token = self._get_refresh_token(stamp, authorization_code)
        valid_until = dt.datetime.now(pytz.utc) + dt.timedelta(hours=23)

        return Token(
            username=username,
            password=password,
            access_token=access_token,
            refresh_token=refresh_token,
            device_id=device_id,
            valid_until=valid_until,
        )

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
            + "oauth2/redirect"
        )

        _LOGGER.debug(f"{DOMAIN} - Get cookies request: {url}")
        session = requests.Session()
        _ = session.get(url)
        _LOGGER.debug(f"{DOMAIN} - Get cookies response: {session.cookies.get_dict()}")
        return session.cookies.get_dict()
        # return session

    def _get_location(self, token: Token, vehicle: Vehicle) -> dict:
        url = self.SPA_API_URL + "vehicles/" + vehicle.id + "/location"

        try:
            response = requests.get(
                url, headers=self._get_authenticated_headers(token)
            ).json()
            _LOGGER.debug(f"{DOMAIN} - _get_location response: {response}")
            _check_response_for_errors(response)
            return response["resMsg"]["gpsDetail"]
        except:
            _LOGGER.warning(f"{DOMAIN} - _get_location failed")
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
        url = self.SPA_API_URL + "vehicles/" + vehicle.id + "/control/door"

        payload = {"action": action.value, "deviceId": token.device_id}
        _LOGGER.debug(f"{DOMAIN} - Lock Action Request: {payload}")
        response = requests.post(
            url, json=payload, headers=self._get_authenticated_headers(token)
        ).json()
        _LOGGER.debug(f"{DOMAIN} - Lock Action Response: {response}")
        _check_response_for_errors(response)
        return response["msgId"]

    def charge_port_action(
        self, token: Token, vehicle: Vehicle, action: CHARGE_PORT_ACTION
    ) -> str:
        url = self.SPA_API_URL_V2 + "vehicles/" + vehicle.id + "/control/portdoor"

        payload = {"action": action.value, "deviceId": token.device_id}
        _LOGGER.debug(f"{DOMAIN} - Charge Port Action Request: {payload}")
        response = requests.post(
            url, json=payload, headers=self._get_authenticated_headers(token)
        ).json()
        _LOGGER.debug(f"{DOMAIN} - Charge Port Action Response: {response}")
        _check_response_for_errors(response)
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
            url, json=payload, headers=self._get_authenticated_headers(token)
        ).json()
        _LOGGER.debug(f"{DOMAIN} - Start Climate Action Response: {response}")
        _check_response_for_errors(response)
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
            url, json=payload, headers=self._get_authenticated_headers(token)
        ).json()
        _LOGGER.debug(f"{DOMAIN} - Stop Climate Action Response: {response}")
        _check_response_for_errors(response)
        return response["msgId"]

    def start_charge(self, token: Token, vehicle: Vehicle) -> str:
        url = self.SPA_API_URL + "vehicles/" + vehicle.id + "/control/charge"

        payload = {"action": "start", "deviceId": token.device_id}
        _LOGGER.debug(f"{DOMAIN} - Start Charge Action Request: {payload}")
        response = requests.post(
            url, json=payload, headers=self._get_authenticated_headers(token)
        ).json()
        _LOGGER.debug(f"{DOMAIN} - Start Charge Action Response: {response}")
        _check_response_for_errors(response)
        return response["msgId"]

    def stop_charge(self, token: Token, vehicle: Vehicle) -> str:
        url = self.SPA_API_URL + "vehicles/" + vehicle.id + "/control/charge"

        payload = {"action": "stop", "deviceId": token.device_id}
        _LOGGER.debug(f"{DOMAIN} - Stop Charge Action Request {payload}")
        response = requests.post(
            url, json=payload, headers=self._get_authenticated_headers(token)
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
        if get_child_value(responseAlltime, "resMsg.drivingInfo.0"):
            drivingInfo = responseAlltime["resMsg"]["drivingInfo"][0]

            drivingInfo["dailyStats"] = []
            if get_child_value(response30d, "resMsg.drivingInfoDetail.0"):
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
            url, json=body, headers=self._get_authenticated_headers(token)
        ).json()
        _LOGGER.debug(f"{DOMAIN} - Set Charge Limits Response: {response}")
        _check_response_for_errors(response)
        return response["msgId"]


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
                        _LOGGER.info(
                            "Action status not set yet by server - try again in a few seconds"  # noqa
                        )
                        return OrderStatus.PENDING

            # if iterate the whole notifications list and
            # can't find the action, raise an exception
            raise APIError(f"No action found with ID {action_id}")

    def update_vehicle_with_cached_state(self, token: Token, vehicle: Vehicle) -> None:
        state = self._get_cached_vehicle_state(token, vehicle)
        self._update_vehicle_properties(vehicle, state)

    def _get_cached_vehicle_state(self, token: Token, vehicle: Vehicle) -> dict:
        url = self.SPA_API_URL + "vehicles/" + vehicle.id + "/status/latest"

        response = requests.get(
            url, headers=self._get_authenticated_headers(token)
        ).json()
        _LOGGER.debug(f"{DOMAIN} - get_cached_vehicle_status response: {response}")
        _check_response_for_errors(response)
        response = response["resMsg"]

        return response

    def _update_vehicle_properties(self, vehicle: Vehicle, state: dict) -> None:
        if get_child_value(state, "time"):
            vehicle.last_updated_at = self.get_last_updated_at(
                get_child_value(state, "time")
            )
        else:
            vehicle.last_updated_at = dt.datetime.now(self.data_timezone)

        vehicle.engine_is_running = get_child_value(state, "engine")

        # Converts temp to usable number. Currently only support celsius.
        # Future to do is check unit in case the care itself is set to F.
        if get_child_value(state, "airTemp.value"):
            tempIndex = get_hex_temp_into_index(
                get_child_value(state, "airTemp.value")
            )

            vehicle.air_temperature = (
                self.temperature_range[tempIndex],
                TEMPERATURE_UNITS[
                    get_child_value(
                        state,
                        "airTemp.unit",
                    )
                ],
            )
        vehicle.defrost_is_on = get_child_value(state, "defrost")
        steer_wheel_heat = get_child_value(state, "steerWheelHeat")
        if steer_wheel_heat in [0, 2]:
            vehicle.steering_wheel_heater_is_on = False
        elif steer_wheel_heat == 1:
            vehicle.steering_wheel_heater_is_on = True

        vehicle.back_window_heater_is_on = get_child_value(
            state, "sideBackWindowHeat"
        )
        vehicle.front_left_seat_status = SEAT_STATUS[
            get_child_value(state, "seatHeaterVentState.astSeatHeatState")
        ]
        vehicle.front_right_seat_status = SEAT_STATUS[
            get_child_value(state, "seatHeaterVentState.drvSeatHeatState")
        ]
        vehicle.rear_left_seat_status = SEAT_STATUS[
            get_child_value(state, "seatHeaterVentState.rlSeatHeatState")
        ]
        vehicle.rear_right_seat_status = SEAT_STATUS[
            get_child_value(state, "seatHeaterVentState.rrSeatHeatState")
        ]
        vehicle.is_locked = get_child_value(state, "doorLock")
        vehicle.front_left_door_is_open = get_child_value(
            state, "doorOpen.frontLeft"
        )
        vehicle.front_right_door_is_open = get_child_value(
            state, "doorOpen.frontRight"
        )
        vehicle.back_left_door_is_open = get_child_value(
            state, "doorOpen.backLeft"
        )
        vehicle.back_right_door_is_open = get_child_value(
            state, "doorOpen.backRight"
        )
        vehicle.hood_is_open = get_child_value(state, "hoodOpen")
        vehicle.front_left_window_is_open = get_child_value(
            state, "windowOpen.frontLeft"
        )
        vehicle.front_right_window_is_open = get_child_value(
            state, "windowOpen.frontRight"
        )
        vehicle.back_left_window_is_open = get_child_value(
            state, "windowOpen.backLeft"
        )
        vehicle.back_right_window_is_open = get_child_value(
            state, "windowOpen.backRight"
        )
        vehicle.tire_pressure_rear_left_warning_is_on = bool(
            get_child_value(state, "tirePressureLamp.tirePressureLampRL")
        )
        vehicle.tire_pressure_front_left_warning_is_on = bool(
            get_child_value(state, "tirePressureLamp.tirePressureLampFL")
        )
        vehicle.tire_pressure_front_right_warning_is_on = bool(
            get_child_value(state, "tirePressureLamp.tirePressureLampFR")
        )
        vehicle.tire_pressure_rear_right_warning_is_on = bool(
            get_child_value(state, "tirePressureLamp.tirePressureLampRR")
        )
        vehicle.tire_pressure_all_warning_is_on = bool(
            get_child_value(state, "tirePressureLamp.tirePressureLampAll")
        )
        vehicle.trunk_is_open = get_child_value(state, "trunkOpen")
        if get_child_value(
            state,
            "dte.value",
        ):
            vehicle.fuel_driving_range = (
                get_child_value(
                    state,
                    "dte.value",
                ),
                DISTANCE_UNITS[get_child_value(state, "dte.unit")],
            )

        vehicle.brake_fluid_warning_is_on = get_child_value(
            state, "breakOilStatus"
        )
        vehicle.fuel_level = get_child_value(state, "fuelLevel")
        vehicle.fuel_level_is_low = get_child_value(state, "lowFuelLight")
        vehicle.air_control_is_on = get_child_value(state, "airCtrlOn")
        vehicle.smart_key_battery_warning_is_on = get_child_value(
            state, "smartKeyBatteryWarning"
        )

        vehicle.data = state

    def _get_time_from_string(self, value, timesection) -> dt.datetime.time:
        if value is not None:
            lastTwo = int(value[-2:])
            if lastTwo > 60:
                value = int(value) + 40
            if int(value) > 1260:
                value = dt.datetime.strptime(str(value), "%H%M").time()
            else:
                d = dt.datetime.strptime(value, "%I%M")
                if timesection > 0:
                    d += dt.timedelta(hours=12)
                value = d.time()
        return value

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
            )
            result.append(vehicle)
        return result

    def get_last_updated_at(self, value) -> dt.datetime:
        _LOGGER.debug(f"{DOMAIN} - last_updated_at - before {value}")
        if value is None:
            value = dt.datetime(2000, 1, 1, tzinfo=self.data_timezone)
        else:
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
