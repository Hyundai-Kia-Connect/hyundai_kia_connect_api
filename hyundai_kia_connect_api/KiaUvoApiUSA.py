"""KiaUvoAPIUSA.py"""

# pylint:disable=logging-fstring-interpolation,unused-argument,missing-timeout,bare-except,missing-function-docstring,invalid-name,unnecessary-pass,broad-exception-raised
import datetime as dt
import logging
import random
import secrets
import ssl
import string
import time
import typing as ty
from datetime import datetime

import certifi
import requests
from requests import RequestException, Response
from requests.adapters import HTTPAdapter
from urllib3.util.ssl_ import create_urllib3_context

from .ApiImpl import ApiImpl, ClimateRequestOptions
from .Token import Token
from .Vehicle import Vehicle
from .const import (
    DISTANCE_UNITS,
    DOMAIN,
    LOGIN_TOKEN_LIFETIME,
    ORDER_STATUS,
    TEMPERATURE_UNITS,
    VEHICLE_LOCK_ACTION,
)
from .utils import get_child_value, parse_datetime
from .exceptions import AuthenticationError


_LOGGER = logging.getLogger(__name__)


# This is the key part of our patch. We get the standard SSLContext that requests would
# normally use, and add ciphers that Kia USA may need for compatibility.
class KiaSSLAdapter(HTTPAdapter):
    def init_poolmanager(self, *args, **kwargs):
        context = create_urllib3_context(
            ciphers="DEFAULT:@SECLEVEL=1", ssl_version=ssl.PROTOCOL_TLSv1_2
        )
        context.options |= 0x4
        kwargs["ssl_context"] = context
        kwargs["ca_certs"] = certifi.where()
        return super().init_poolmanager(*args, **kwargs)


class AuthError(RequestException):
    """AuthError"""

    pass


def request_with_active_session(func):
    def request_with_active_session_wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except AuthError:
            _LOGGER.debug(
                f"{DOMAIN} - Got invalid session, attempting to repair and resend"
            )
            self = args[0]
            token = kwargs["token"]
            vehicle = kwargs["vehicle"]
            new_token = self.login(
                token.username,
                token.password,
                token,
                getattr(self, "_otp_handler", None),
            )
            token.access_token = new_token.access_token
            token.refresh_token = new_token.refresh_token
            token.valid_until = new_token.valid_until
            json_body = kwargs.get("json_body", None)
            vehicle = self.refresh_vehicles(token, vehicle)
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
            and response_json["status"]["errorCode"] in [1003, 1005]
        ):
            _LOGGER.debug(f"{DOMAIN} - Error: session invalid")
            raise AuthError
        _LOGGER.error(f"{DOMAIN} - Error: unknown error response {response.text}")
        raise RequestException

    return request_with_logging_wrapper


class KiaUvoApiUSA(ApiImpl):
    """KiaUvoApiUSA"""

    def __init__(self, region: int, brand: int, language) -> None:
        self.LANGUAGE: str = language
        self.temperature_range = range(62, 83)

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
        self._session = None

        self._otp_handler = None
        self.supports_otp: bool = True

    @property
    def session(self):
        if not self._session:
            self._session = requests.Session()
            self._session.mount("https://", KiaSSLAdapter())
        return self._session

    def api_headers(self) -> dict:
        offset = time.localtime().tm_gmtoff / 60 / 60
        headers = {
            "content-type": "application/json;charset=UTF-8",
            "accept": "application/json, text/plain, */*",
            "accept-encoding": "gzip, deflate, br",
            "accept-language": "en-US,en;q=0.9",
            "apptype": "L",
            "appversion": "7.15.2",
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
            "user-agent": "okhttp/4.10.0",
        }
        # Should produce something like "Mon, 18 Oct 2021 07:06:26 GMT".
        # May require adjusting locale to en_US
        date = datetime.now(tz=dt.timezone.utc).strftime("%a, %d %b %Y %H:%M:%S GMT")
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
        return self.session.post(url, json=json_body, headers=headers)

    @request_with_active_session
    @request_with_logging
    def get_request_with_logging_and_active_session(
        self, token: Token, url: str, vehicle: Vehicle
    ) -> Response:
        headers = self.authed_api_headers(token, vehicle)
        return self.session.get(url, headers=headers)

    def _send_otp(self, otp_key: str, notify_type: str, xid: str) -> dict:
        """Send OTP to email or phone"""
        url = self.API_URL + "cmm/sendOTP"
        headers = self.api_headers()
        headers["otpkey"] = otp_key
        headers["notifytype"] = notify_type
        headers["xid"] = xid
        response = self.session.post(url, json={}, headers=headers)
        _LOGGER.debug(f"{DOMAIN} - Send OTP Response {response.text}")
        return response.json()

    def _verify_otp(self, otp_key: str, otp_code: str, xid: str) -> tuple[str, str]:
        """Verify OTP code and return sid and rmtoken"""
        url = self.API_URL + "cmm/verifyOTP"
        headers = self.api_headers()
        headers["otpkey"] = otp_key
        headers["xid"] = xid
        data = {"otp": otp_code}
        response = self.session.post(url, json=data, headers=headers)
        _LOGGER.debug(f"{DOMAIN} - Verify OTP Response {response.text}")
        response_json = response.json()
        if response_json["status"]["statusCode"] != 0:
            raise Exception(
                f"{DOMAIN} - OTP verification failed: {response_json['status']['errorMessage']}"
            )
        sid = response.headers.get("sid")
        rmtoken = response.headers.get("rmtoken")
        if not sid or not rmtoken:
            raise Exception(
                f"{DOMAIN} - No sid or rmtoken in OTP verification response. Headers: {response.headers}"
            )
        return sid, rmtoken

    def _complete_login_with_otp(
        self, username: str, password: str, sid: str, rmtoken: str
    ) -> str:
        """Complete login with sid and rmtoken to get final session id"""
        url = self.API_URL + "prof/authUser"
        data = {
            "deviceKey": "",
            "deviceType": 2,
            "userCredential": {"userId": username, "password": password},
        }
        headers = self.api_headers()
        headers["sid"] = sid
        headers["rmtoken"] = rmtoken
        response = self.session.post(url, json=data, headers=headers)
        _LOGGER.debug(f"{DOMAIN} - Complete Login Response {response.text}")
        final_sid = response.headers.get("sid")
        if not final_sid:
            raise Exception(
                f"{DOMAIN} - No final sid returned. Response: {response.text}"
            )
        return final_sid

    def start_login(
        self,
        username: str,
        password: str,
        token: Token | None = None,
    ) -> tuple[Token | None, dict | None]:
        """Start login and return either a Token or an OTP context.

        Parameters
        ----------
        username : str
            User email address
        password : str
            User password
        token : Token | None
            Existing token with stored rmtoken for reuse

        Returns
        -------
        tuple[Token | None, dict | None]
            (Token, None) if login succeeded without OTP, otherwise (None, ctx)
            where ctx contains 'otpKey', 'xid', 'email', 'phone', 'hasEmail', 'hasPhone'.
        """
        url = self.API_URL + "prof/authUser"
        data = {
            "deviceKey": "",
            "deviceType": 2,
            "userCredential": {"userId": username, "password": password},
        }
        headers = self.api_headers()
        if token and getattr(token, "device_id", None):
            self.device_id = token.device_id

        if token and token.refresh_token:
            _LOGGER.debug(f"{DOMAIN} - Attempting start_login with stored rmtoken")
            headers["rmtoken"] = token.refresh_token
        response = self.session.post(url, json=data, headers=headers)
        _LOGGER.debug(f"{DOMAIN} - Start Sign In Response {response.text}")
        response_json = response.json()
        session_id = response.headers.get("sid")
        if session_id:
            _LOGGER.debug(f"got session id {session_id}")
            valid_until = dt.datetime.now(dt.timezone.utc) + LOGIN_TOKEN_LIFETIME
            existing_rmtoken = token.refresh_token if token else None
            return (
                Token(
                    username=username,
                    password=password,
                    access_token=session_id,
                    refresh_token=existing_rmtoken,
                    valid_until=valid_until,
                    device_id=self.device_id,
                ),
                None,
            )
        if "payload" in response_json and "otpKey" in response_json["payload"]:
            payload = response_json["payload"]
            xid = response.headers.get("xid", "")
            ctx = {
                "otpKey": payload["otpKey"],
                "xid": xid,
                "email": payload.get("email"),
                "phone": payload.get("phone"),
                "hasEmail": bool(payload.get("hasEmail")),
                "hasPhone": bool(payload.get("hasPhone")),
                "rmTokenExpired": bool(payload.get("rmTokenExpired")),
            }
            return None, ctx
        raise Exception(
            f"{DOMAIN} - No session id returned in start_login. Response: {response.text}"
        )

    def send_otp(self, otp_key: str, notify_type: str, xid: str) -> dict:
        """Public helper to send OTP to the selected destination."""
        return self._send_otp(otp_key, notify_type, xid)

    def verify_otp_and_complete_login(
        self,
        username: str,
        password: str,
        otp_key: str,
        xid: str,
        otp_code: str,
    ) -> Token:
        """Verify OTP and complete the login producing a Token."""
        sid, rmtoken = self._verify_otp(otp_key, otp_code, xid)
        final_sid = self._complete_login_with_otp(username, password, sid, rmtoken)
        _LOGGER.debug(f"got final session id {final_sid}")
        _LOGGER.info(f"{DOMAIN} - Storing rmtoken for future logins")
        valid_until = dt.datetime.now(dt.timezone.utc) + LOGIN_TOKEN_LIFETIME
        return Token(
            username=username,
            password=password,
            access_token=final_sid,
            refresh_token=rmtoken,
            valid_until=valid_until,
            device_id=self.device_id,
        )

    def login(
        self,
        username: str,
        password: str,
        token: Token = None,
        otp_handler: ty.Callable[[dict], dict] | None = None,
    ) -> Token:
        """Login into cloud endpoints and return Token

        Parameters
        ----------
        username : str
            User email address
        password : str
            User password
        token : Token, optional
            Existing token with stored rmtoken for reuse
        otp_handler : Callable[[dict], dict], optional
            Non-interactive OTP handler. Called twice:
            - stage='choose_destination' -> return {'notify_type': 'EMAIL'|'PHONE'}
            - stage='input_code' -> return {'otp_code': '<code>'}

        Returns
        -------
        Token
            Token object with access_token (sid) and refresh_token (rmtoken)
        """
        url = self.API_URL + "prof/authUser"
        data = {
            "deviceKey": "",
            "deviceType": 2,
            "userCredential": {"userId": username, "password": password},
        }
        if token and getattr(token, "device_id", None):
            self.device_id = token.device_id
        if otp_handler is not None:
            self._otp_handler = otp_handler
        headers = self.api_headers()
        if token and token.refresh_token:
            _LOGGER.debug(f"{DOMAIN} - Attempting login with stored rmtoken")
            headers["rmtoken"] = token.refresh_token
        response = self.session.post(url, json=data, headers=headers)
        _LOGGER.debug(f"{DOMAIN} - Sign In Response {response.text}")
        response_json = response.json()
        session_id = response.headers.get("sid")
        if session_id:
            _LOGGER.debug(f"got session id {session_id}")
            valid_until = dt.datetime.now(dt.timezone.utc) + LOGIN_TOKEN_LIFETIME
            existing_rmtoken = token.refresh_token if token else None
            return Token(
                username=username,
                password=password,
                access_token=session_id,
                refresh_token=existing_rmtoken,
                valid_until=valid_until,
                device_id=self.device_id,
            )
        if "payload" in response_json and "otpKey" in response_json["payload"]:
            payload = response_json["payload"]
            if payload.get("rmTokenExpired"):
                _LOGGER.info(f"{DOMAIN} - Stored rmtoken has expired, need new OTP")
            otp_key = payload["otpKey"]
            xid = response.headers.get("xid", "")
            _LOGGER.info(f"{DOMAIN} - OTP required for login")
            _LOGGER.info(f"{DOMAIN} - Email: {payload.get('email', 'N/A')}")
            _LOGGER.info(f"{DOMAIN} - Phone: {payload.get('phone', 'N/A')}")
            notify_type = "EMAIL"
            handler = otp_handler or getattr(self, "_otp_handler", None)
            if handler:
                try:
                    ctx_choice = {
                        "stage": "choose_destination",
                        "hasEmail": bool(payload.get("hasEmail")),
                        "hasPhone": bool(payload.get("hasPhone")),
                        "email": payload.get("email"),
                        "phone": payload.get("phone"),
                    }
                    res = handler(ctx_choice) or {}
                    nt = str(res.get("notify_type", notify_type)).upper()
                    if nt in ("EMAIL", "PHONE"):
                        notify_type = nt
                except Exception:
                    _LOGGER.debug(
                        f"{DOMAIN} - otp_handler choose_destination failed; using default"
                    )
            else:
                if payload.get("hasEmail") and payload.get("hasPhone"):
                    print("\nOTP Authentication Required")
                    print(f"Email: {payload.get('email', 'N/A')}")
                    print(f"Phone: {payload.get('phone', 'N/A')}")
                    choice = (
                        input("Send OTP to (E)mail or (P)hone? [E/P]: ").strip().upper()
                    )
                    if choice == "P":
                        notify_type = "PHONE"
                elif payload.get("hasPhone"):
                    notify_type = "PHONE"
            self._send_otp(otp_key, notify_type, xid)
            if not handler:
                print(f"\nOTP sent to {notify_type.lower()}")
            otp_code = None
            if handler:
                try:
                    ctx_code = {
                        "stage": "input_code",
                        "notify_type": notify_type,
                        "otpKey": otp_key,
                        "xid": xid,
                    }
                    res2 = handler(ctx_code) or {}
                    otp_code = str(res2.get("otp_code", "")).strip()
                except Exception:
                    _LOGGER.debug(f"{DOMAIN} - otp_handler input_code failed")
            if not otp_code:
                if handler is None:
                    otp_code = input("Enter OTP code: ").strip()
                else:
                    raise AuthenticationError(f"{DOMAIN} - OTP code required")
            sid, rmtoken = self._verify_otp(otp_key, otp_code, xid)
            final_sid = self._complete_login_with_otp(username, password, sid, rmtoken)
            _LOGGER.debug(f"got final session id {final_sid}")
            _LOGGER.info(f"{DOMAIN} - Storing rmtoken for future logins")
            valid_until = dt.datetime.now(dt.timezone.utc) + LOGIN_TOKEN_LIFETIME
            return Token(
                username=username,
                password=password,
                access_token=final_sid,
                refresh_token=rmtoken,
                valid_until=valid_until,
                device_id=self.device_id,
            )
        raise Exception(
            f"{DOMAIN} - No session id returned in login. Response: {response.text} headers {response.headers} cookies {response.cookies}"
        )

    def get_vehicles(self, token: Token) -> list[Vehicle]:
        """Return all Vehicle instances for a given Token"""
        url = self.API_URL + "ownr/gvl"
        headers = self.api_headers()
        headers["sid"] = token.access_token
        response = self.session.get(url, headers=headers)
        _LOGGER.debug(f"{DOMAIN} - Get Vehicles Response {response.text}")
        response = response.json()
        result = []
        for entry in response["payload"]["vehicleSummary"]:
            vehicle: Vehicle = Vehicle(
                id=entry["vehicleIdentifier"],
                name=entry["nickName"],
                model=entry["modelName"],
                key=entry["vehicleKey"],
                timezone=self.data_timezone,
            )
            result.append(vehicle)
        return result

    def refresh_vehicles(
        self, token: Token, vehicles: ty.Union[list[Vehicle], Vehicle]
    ) -> ty.Union[list[Vehicle], Vehicle]:
        """
        Refresh the vehicle data provided in get_vehicles.
        Required for Kia USA as key is session specific
        """
        url = self.API_URL + "ownr/gvl"
        headers = self.api_headers()
        headers["sid"] = token.access_token
        response = self.session.get(url, headers=headers)
        _LOGGER.debug(f"{DOMAIN} - Get Vehicles Response {response.text}")
        _LOGGER.debug(f"{DOMAIN} - Vehicles Type Passed in: {type(vehicles)}")
        _LOGGER.debug(f"{DOMAIN} - Vehicles Passed in: {vehicles}")

        response = response.json()
        if isinstance(vehicles, dict):
            for entry in response["payload"]["vehicleSummary"]:
                vid = entry.get("vehicleIdentifier")
                if vid is None:
                    continue
                vobj = vehicles.get(vid)
                if vobj is not None:
                    vobj.name = entry.get("nickName")
                    vobj.model = entry.get("modelName")
                    vobj.key = entry.get("vehicleKey")
                else:
                    vehicle: Vehicle = Vehicle(
                        id=vid,
                        name=entry.get("nickName"),
                        model=entry.get("modelName"),
                        key=entry.get("vehicleKey"),
                        timezone=self.data_timezone,
                    )
                    vehicles[vid] = vehicle
            return vehicles
        else:
            # For readability work with vehicle without s
            vehicle = vehicles
            for entry in response["payload"]["vehicleSummary"]:
                if vehicle.id == entry["vehicleIdentifier"]:
                    vehicle.name = entry["nickName"]
                    vehicle.model = entry["modelName"]
                    vehicle.key = entry["vehicleKey"]
                    return vehicle

    def update_vehicle_with_cached_state(self, token: Token, vehicle: Vehicle) -> None:
        state = self._get_cached_vehicle_state(token, vehicle)
        self._update_vehicle_properties(vehicle, state)

    def force_refresh_vehicle_state(self, token: Token, vehicle: Vehicle) -> None:
        self._get_forced_vehicle_state(token, vehicle)
        # Force update needs work to return the correct data for processing
        # self._update_vehicle_properties(vehicle, state)
        # Temp call a cached state since we are removing this from parent logic in
        # other commits should be removed when the above is fixed
        self.update_vehicle_with_cached_state(token, vehicle)

    def _update_vehicle_properties(self, vehicle: Vehicle, state: dict) -> None:
        """Get cached vehicle data and update Vehicle instance with it"""
        vehicle.last_updated_at = parse_datetime(
            get_child_value(
                state, "lastVehicleInfo.vehicleStatusRpt.vehicleStatus.syncDate.utc"
            ),
            self.data_timezone,
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
            state,
            "lastVehicleInfo.vehicleStatusRpt.vehicleStatus.batteryStatus.stateOfCharge",  # noqa
        )
        vehicle.engine_is_running = get_child_value(
            state, "lastVehicleInfo.vehicleStatusRpt.vehicleStatus.engine"
        )

        air_temp = get_child_value(
            state,
            "lastVehicleInfo.vehicleStatusRpt.vehicleStatus.climate.airTemp.value",
        )

        if air_temp == "LOW":
            air_temp = self.temperature_range[0]
        if air_temp == "HIGH":
            air_temp = self.temperature_range[-1]
        if air_temp:
            vehicle.air_temperature = (air_temp, TEMPERATURE_UNITS[1])
        vehicle.defrost_is_on = get_child_value(
            state, "lastVehicleInfo.vehicleStatusRpt.vehicleStatus.climate.defrost"
        )
        vehicle.washer_fluid_warning_is_on = get_child_value(
            state, "lastVehicleInfo.vehicleStatusRpt.vehicleStatus.washerFluidStatus"
        )
        vehicle.brake_fluid_warning_is_on = get_child_value(
            state, "lastVehicleInfo.vehicleStatusRpt.vehicleStatus.breakOilStatus"
        )
        vehicle.smart_key_battery_warning_is_on = get_child_value(
            state,
            "lastVehicleInfo.vehicleStatusRpt.vehicleStatus.smartKeyBatteryWarning",
        )
        vehicle.tire_pressure_all_warning_is_on = get_child_value(
            state, "lastVehicleInfo.vehicleStatusRpt.vehicleStatus.tirePressure.all"
        )

        vehicle.steering_wheel_heater_is_on = get_child_value(
            state,
            "lastVehicleInfo.vehicleStatusRpt.vehicleStatus.climate.heatingAccessory.steeringWheel",  # noqa
        )
        vehicle.back_window_heater_is_on = get_child_value(
            state,
            "lastVehicleInfo.vehicleStatusRpt.vehicleStatus.climate.heatingAccessory.rearWindow",  # noqa
        )
        vehicle.side_mirror_heater_is_on = get_child_value(
            state,
            "lastVehicleInfo.vehicleStatusRpt.vehicleStatus.climate.heatingAccessory.sideMirror",  # noqa
        )
        vehicle.front_left_seat_heater_is_on = get_child_value(
            state,
            "lastVehicleInfo.vehicleStatusRpt.vehicleStatus.seatHeaterVentState.flSeatHeatState",  # noqa
        )
        vehicle.front_right_seat_heater_is_on = get_child_value(
            state,
            "lastVehicleInfo.vehicleStatusRpt.vehicleStatus.seatHeaterVentState.frSeatHeatState",  # noqa
        )
        vehicle.rear_left_seat_heater_is_on = get_child_value(
            state,
            "lastVehicleInfo.vehicleStatusRpt.vehicleStatus.seatHeaterVentState.rlSeatHeatState",  # noqa
        )
        vehicle.rear_right_seat_heater_is_on = get_child_value(
            state,
            "lastVehicleInfo.vehicleStatusRpt.vehicleStatus.seatHeaterVentState.rrSeatHeatState",  # noqa
        )
        vehicle.is_locked = get_child_value(
            state, "lastVehicleInfo.vehicleStatusRpt.vehicleStatus.doorLock"
        )
        vehicle.front_left_door_is_open = get_child_value(
            state, "lastVehicleInfo.vehicleStatusRpt.vehicleStatus.doorStatus.frontLeft"
        )
        vehicle.front_right_door_is_open = get_child_value(
            state,
            "lastVehicleInfo.vehicleStatusRpt.vehicleStatus.doorStatus.frontRight",
        )
        vehicle.back_left_door_is_open = get_child_value(
            state, "lastVehicleInfo.vehicleStatusRpt.vehicleStatus.doorStatus.backLeft"
        )
        vehicle.back_right_door_is_open = get_child_value(
            state, "lastVehicleInfo.vehicleStatusRpt.vehicleStatus.doorStatus.backRight"
        )
        vehicle.hood_is_open = get_child_value(
            state, "lastVehicleInfo.vehicleStatusRpt.vehicleStatus.doorStatus.hood"
        )
        vehicle.sunroof_is_open = get_child_value(
            state, "lastVehicleInfo.vehicleStatusRpt.vehicleStatus.sunroofOpen"
        )

        vehicle.trunk_is_open = get_child_value(
            state, "lastVehicleInfo.vehicleStatusRpt.vehicleStatus.doorStatus.trunk"
        )
        vehicle.front_left_window_is_open = get_child_value(
            state, "lastVehicleInfo.vehicleStatusRpt.vehicleStatus.windowOpen.frontLeft"
        )
        vehicle.front_right_window_is_open = get_child_value(
            state,
            "lastVehicleInfo.vehicleStatusRpt.vehicleStatus.windowOpen.frontRight",
        )
        vehicle.back_left_window_is_open = get_child_value(
            state, "lastVehicleInfo.vehicleStatusRpt.vehicleStatus.windowOpen.backLeft"
        )
        vehicle.back_right_window_is_open = get_child_value(
            state, "lastVehicleInfo.vehicleStatusRpt.vehicleStatus.windowOpen.backRight"
        )
        if vehicle.front_left_window_is_open is None:
            vehicle.front_left_window_is_open = get_child_value(
                state,
                "lastVehicleInfo.vehicleStatusRpt.vehicleStatus.evStatus.windowStatus.windowFL",
            )
        if vehicle.front_right_window_is_open is None:
            vehicle.front_right_window_is_open = get_child_value(
                state,
                "lastVehicleInfo.vehicleStatusRpt.vehicleStatus.evStatus.windowStatus.windowFR",
            )
        if vehicle.back_left_window_is_open is None:
            vehicle.back_left_window_is_open = get_child_value(
                state,
                "lastVehicleInfo.vehicleStatusRpt.vehicleStatus.evStatus.windowStatus.windowRL",
            )
        if vehicle.back_right_window_is_open is None:
            vehicle.back_right_window_is_open = get_child_value(
                state,
                "lastVehicleInfo.vehicleStatusRpt.vehicleStatus.evStatus.windowStatus.windowRR",
            )
        vehicle.ev_battery_percentage = get_child_value(
            state,
            "lastVehicleInfo.vehicleStatusRpt.vehicleStatus.evStatus.batteryStatus",
        )
        vehicle.ev_battery_is_charging = get_child_value(
            state,
            "lastVehicleInfo.vehicleStatusRpt.vehicleStatus.evStatus.batteryCharge",
        )
        vehicle.ev_battery_is_plugged_in = get_child_value(
            state,
            "lastVehicleInfo.vehicleStatusRpt.vehicleStatus.evStatus.batteryPlugin",
        )
        vehicle.ev_charging_power = get_child_value(
            state,
            "lastVehicleInfo.vehicleStatusRpt.vehicleStatus.evStatus.realTimePower",
        )
        ChargeDict = get_child_value(
            state, "lastVehicleInfo.vehicleStatusRpt.vehicleStatus.evStatus.targetSOC"
        )
        try:
            vehicle.ev_charge_limits_ac = [
                x["targetSOClevel"] for x in ChargeDict if x["plugType"] == 1
            ][-1]
            vehicle.ev_charge_limits_dc = [
                x["targetSOClevel"] for x in ChargeDict if x["plugType"] == 0
            ][-1]
        except Exception:
            _LOGGER.debug(f"{DOMAIN} - SOC Levels couldn't be found. May not be an EV.")

        vehicle.ev_driving_range = (
            get_child_value(
                state,
                "lastVehicleInfo.vehicleStatusRpt.vehicleStatus.evStatus.drvDistance.0.rangeByFuel.evModeRange.value",  # noqa
            ),
            DISTANCE_UNITS[
                get_child_value(
                    state,
                    "lastVehicleInfo.vehicleStatusRpt.vehicleStatus.evStatus.drvDistance.0.rangeByFuel.evModeRange.unit",  # noqa
                )
            ],
        )
        vehicle.ev_estimated_current_charge_duration = (
            get_child_value(
                state,
                "lastVehicleInfo.vehicleStatusRpt.vehicleStatus.evStatus.remainChargeTime.0.timeInterval.value",  # noqa
            ),
            "m",
        )
        vehicle.ev_estimated_fast_charge_duration = (
            get_child_value(
                state,
                "lastVehicleInfo.vehicleStatusRpt.vehicleStatus.evStatus.remainChargeTime.0.etc1.value",  # noqa
            ),
            "m",
        )
        vehicle.ev_estimated_portable_charge_duration = (
            get_child_value(
                state,
                "lastVehicleInfo.vehicleStatusRpt.vehicleStatus.evStatus.remainChargeTime.0.etc2.value",  # noqa
            ),
            "m",
        )
        vehicle.ev_estimated_station_charge_duration = (
            get_child_value(
                state,
                "lastVehicleInfo.vehicleStatusRpt.vehicleStatus.evStatus.remainChargeTime.0.etc3.value",  # noqa
            ),
            "m",
        )
        vehicle.total_driving_range = (
            get_child_value(
                state,
                "lastVehicleInfo.vehicleStatusRpt.vehicleStatus.evStatus.drvDistance.0.rangeByFuel.totalAvailableRange.value",  # noqa
            ),
            DISTANCE_UNITS[
                get_child_value(
                    state,
                    "lastVehicleInfo.vehicleStatusRpt.vehicleStatus.evStatus.drvDistance.0.rangeByFuel.totalAvailableRange.unit",  # noqa
                )
            ],
        )
        if get_child_value(
            state,
            "lastVehicleInfo.vehicleStatusRpt.vehicleStatus.evStatus.drvDistance.0.rangeByFuel.gasModeRange.value",  # noqa
        ):
            vehicle.fuel_driving_range = (
                get_child_value(
                    state,
                    "lastVehicleInfo.vehicleStatusRpt.vehicleStatus.evStatus.drvDistance.0.rangeByFuel.gasModeRange.value",  # noqa
                ),
                DISTANCE_UNITS[
                    get_child_value(
                        state,
                        "lastVehicleInfo.vehicleStatusRpt.vehicleStatus.evStatus.drvDistance.0.rangeByFuel.gasModeRange.unit",  # noqa
                    )
                ],
            )
        else:
            vehicle.fuel_driving_range = (
                get_child_value(
                    state,
                    "lastVehicleInfo.vehicleStatusRpt.vehicleStatus.distanceToEmpty.value",  # noqa
                ),
                DISTANCE_UNITS[
                    get_child_value(
                        state,
                        "lastVehicleInfo.vehicleStatusRpt.vehicleStatus.distanceToEmpty.unit",  # noqa
                    )
                ],
            )
        vehicle.fuel_level_is_low = get_child_value(
            state, "lastVehicleInfo.vehicleStatusRpt.vehicleStatus.lowFuelLight"
        )
        vehicle.fuel_level = get_child_value(
            state, "lastVehicleInfo.vehicleStatusRpt.vehicleStatus.fuelLevel"
        )
        vehicle.air_control_is_on = get_child_value(
            state, "lastVehicleInfo.vehicleStatusRpt.vehicleStatus.climate.airCtrl"
        )

        if get_child_value(state, "lastVehicleInfo.location.coord.lat"):
            vehicle.location = (
                get_child_value(state, "lastVehicleInfo.location.coord.lat"),
                get_child_value(state, "lastVehicleInfo.location.coord.lon"),
                parse_datetime(
                    get_child_value(state, "lastVehicleInfo.location.syncDate.utc"),
                    self.data_timezone,
                ),
            )

        vehicle.next_service_distance = (
            get_child_value(state, "vehicleConfig.maintenance.nextServiceMile"),
            DISTANCE_UNITS[3],
        )

        vehicle.dtc_count = get_child_value(
            state, "lastVehicleInfo.activeDTC.dtcActiveCount"
        )
        vehicle.dtc_descriptions = get_child_value(
            state, "lastVehicleInfo.activeDTC.dtcCategory"
        )

        vehicle.data = state

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
            "requestType": 0
            # value of 1 would return cached results instead of forcing update
        }
        response = self.post_request_with_logging_and_active_session(
            token=token, url=url, json_body=body, vehicle=vehicle
        )
        response_body = response.json()
        return response_body

    def check_action_status(
        self,
        token: Token,
        vehicle: Vehicle,
        action_id: str,
        synchronous: bool = False,
        timeout: int = 0,
    ) -> ORDER_STATUS:
        url = self.API_URL + "cmm/gts"
        body = {"xid": action_id}
        response = self.post_request_with_logging_and_active_session(
            token=token, url=url, json_body=body, vehicle=vehicle
        )
        response_json = response.json()
        last_action_completed = all(v == 0 for v in response_json["payload"].values())
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

    def _seat_settings(self, level) -> dict:
        # See const.SEAT_STATUS for the list and descriptions of levels.
        #
        # The values were determined empirically, see https://github.com/Hyundai-Kia-Connect/kia_uvo/issues/718
        if level == 8:  # High heat
            return {
                "heatVentType": 1,
                "heatVentLevel": 4,
                "heatVentStep": 1,
            }
        elif level == 7:  # Medium heat
            return {
                "heatVentType": 1,
                "heatVentLevel": 3,
                "heatVentStep": 2,
            }
        elif level == 6:  # Low heat
            return {
                "heatVentType": 1,
                "heatVentLevel": 2,
                "heatVentStep": 3,
            }
        elif level == 5:  # High cool
            return {
                "heatVentType": 2,
                "heatVentLevel": 4,
                "heatVentStep": 1,
            }
        elif level == 4:  # Medium cool
            return {
                "heatVentType": 2,
                "heatVentLevel": 3,
                "heatVentStep": 2,
            }
        elif level == 3:  # Low cool
            return {
                "heatVentType": 2,
                "heatVentLevel": 2,
                "heatVentStep": 3,
            }
        elif level == 1:  # Generically on, let's assume high heat
            return {
                "heatVentType": 1,
                "heatVentLevel": 4,
                "heatVentStep": 1,
            }
        else:  # Off
            return {
                "heatVentType": 0,
                "heatVentLevel": 1,
                "heatVentStep": 0,
            }

    def start_climate(
        self, token: Token, vehicle: Vehicle, options: ClimateRequestOptions
    ) -> str:
        url = self.API_URL + "rems/start"
        if options.set_temp is None:
            options.set_temp = 70
        if options.set_temp < 62:
            options.set_temp = "LOW"
        elif options.set_temp > 82:
            options.set_temp = "HIGH"
        if options.climate is None:
            options.climate = True
        if options.heating is None:
            options.heating = 0
        if options.defrost is None:
            options.defrost = False
        if options.duration is None:
            options.duration = 5
        if options.steering_wheel is None:
            options.steering_wheel = 0

        body = {
            "remoteClimate": {
                "airTemp": {
                    "unit": 1,
                    "value": str(options.set_temp),
                },
                "airCtrl": options.climate,
                "defrost": options.defrost,
                "heatingAccessory": {
                    "rearWindow": 1 if options.heating in [1, 2, 4] else 0,
                    "sideMirror": 1 if options.heating in [1, 4] else 0,
                    "steeringWheel": 1 if options.steering_wheel in [1, 2] else 0,
                    "steeringWheelStep": options.steering_wheel,
                },
                "ignitionOnDuration": {
                    "unit": 4,
                    "value": options.duration,
                },
            },
        }

        # Kia seems to now be checking if you can set the heated/vented seats at
        # the car level only add to body if the option is not none for any of
        # the seats
        if (
            options.front_left_seat is not None
            or options.front_right_seat is not None
            or options.rear_left_seat is not None
            or options.rear_right_seat is not None
        ):
            body["remoteClimate"]["heatVentSeat"] = {
                "driverSeat": self._seat_settings(options.front_left_seat),
                "passengerSeat": self._seat_settings(options.front_right_seat),
                "rearLeftSeat": self._seat_settings(options.rear_left_seat),
                "rearRightSeat": self._seat_settings(options.rear_right_seat),
            }
        _LOGGER.debug(f"{DOMAIN} - Planned start_climate payload: {body}")
        response = self.post_request_with_logging_and_active_session(
            token=token, url=url, json_body=body, vehicle=vehicle
        )
        return response.headers["Xid"]

    def stop_climate(self, token: Token, vehicle: Vehicle) -> str:
        url = self.API_URL + "rems/stop"
        response = self.get_request_with_logging_and_active_session(
            token=token, url=url, vehicle=vehicle
        )
        return response.headers["Xid"]

    def start_charge(self, token: Token, vehicle: Vehicle) -> str:
        url = self.API_URL + "evc/charge"
        body = {"chargeRatio": 100}
        response = self.post_request_with_logging_and_active_session(
            token=token, url=url, json_body=body, vehicle=vehicle
        )
        return response.headers["Xid"]

    def stop_charge(self, token: Token, vehicle: Vehicle) -> str:
        url = self.API_URL + "evc/cancel"
        response = self.get_request_with_logging_and_active_session(
            token=token, url=url, vehicle=vehicle
        )
        return response.headers["Xid"]

    def set_charge_limits(
        self, token: Token, vehicle: Vehicle, ac: int, dc: int
    ) -> str:
        url = self.API_URL + "evc/sts"
        body = {
            "targetSOClist": [
                {
                    "plugType": 0,
                    "targetSOClevel": int(dc),
                },
                {
                    "plugType": 1,
                    "targetSOClevel": int(ac),
                },
            ]
        }
        response = self.post_request_with_logging_and_active_session(
            token=token, url=url, json_body=body, vehicle=vehicle
        )
        return response.headers["Xid"]
