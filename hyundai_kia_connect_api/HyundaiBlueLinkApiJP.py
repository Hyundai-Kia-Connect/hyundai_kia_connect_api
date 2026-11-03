"""Hyundai Japan Bluelink adapter.

Contracts are based on the Japan app and its public login page. Vehicle
Vehicle controls use request-correlated MQTT completion rather than HTTP ACKs.
"""

import base64
import copy
import datetime as dt
import math
import re
import time
import uuid
from typing import ClassVar
from urllib.parse import parse_qs, urljoin, urlparse
from zoneinfo import ZoneInfo

import requests

from .ApiImpl import ApiImpl, ApiImplSession
from .ApiImplType1 import ApiImplType1
from .const import ENGINE_TYPES, ORDER_STATUS, SEAT_STATUS
from .exceptions import (
    APIError,
    AuthenticationError,
    ConsentRequiredError,
    DuplicateRequestError,
    InvalidAPIResponseError,
    NoDataFound,
    RateLimitingError,
    RequestTimeoutError,
    ServiceTemporaryUnavailable,
)
from .japan_commands import JapanCommandsMixin
from .japan_mqtt import JapanMqttSession
from .japan_svm import JapanSVM
from .Token import Token
from .utils import get_child_value, parse_datetime
from .Vehicle import DailyDrivingStats, Vehicle


class HyundaiBlueLinkApiJP(JapanCommandsMixin, JapanSVM, ApiImpl):
    """Japan status, profile-aware features, and correlated vehicle controls."""

    data_timezone = ZoneInfo("Asia/Tokyo")
    supports_remote_commands = True
    supports_force_refresh = True
    requires_command_confirmation = True
    FORCE_REFRESH_TIMEOUT = 120
    supported_commands = frozenset(
        {
            "request_svm_capture",
            "lock_action",
            "start_charge",
            "stop_charge",
            "start_climate",
            "stop_climate",
            "set_charge_limits",
            "set_charging_current",
            "set_windows_state",
            "charge_port_action",
            "start_hazard_lights",
            "start_hazard_lights_and_horn",
            "set_vehicle_to_load_discharge_limit",
            "schedule_charging_and_climate",
            "valet_mode_action",
            "set_navigation",
        }
    )
    supports_svm = True
    supports_window_control = True
    supports_valet_mode = True
    BASE_URL = "https://ccs-apigw.hyundaijapan.com:8080"
    SERVICE_ID = "e084c0e4-fcfe-47e3-99c9-36eb93593737"
    APP_ID = "8be122e0-b0d9-40b5-a46e-67b25194ba88"
    REDIRECT_URI = BASE_URL + "/api/v1/user/oauth2/redirect"
    # CFB128 keystream for the fixed UUID prefix and a ten-digit epoch.
    # Derived offline from Japan app 1.0.17; no app binary is distributed.
    _STAMP_STREAM = base64.b64decode(
        "yGeH2vkxepxXpBulEUnI04QXO3CM9oT4E20p+AB8lqHuRZmVEfdQlJmzBX5z5pk="
    )
    BASIC_AUTHORIZATION = "Basic ZTA4NGMwZTQtZmNmZS00N2UzLTk5YzktMzZlYjkzNTkzNzM3OjFZcWV4dng3TWxiS3c2YUFvYk1FbDJqQVFvQTBtUVhyV1E1SWpIT083TzRZRHRkRw=="

    # Reuse only pure schema parsers, never Type1 HTTP/logging helpers.
    _update_vehicle_properties = ApiImplType1._update_vehicle_properties
    _get_time_from_string = ApiImplType1._get_time_from_string

    def __init__(self, region: int, brand: int, language: str) -> None:
        if region != 10 or brand != 2:
            raise APIError("Japan is supported only for Hyundai in region 10")
        super().__init__()
        self.session = ApiImplSession()
        self._vehicle_profiles: dict[str, dict] = {}
        self._history_updated_at: dict[str, float] = {}
        self._location_missing_at: dict[str, float] = {}
        self._charge_target_cache: dict[str, tuple[float, dict]] = {}
        self._mqtt_sessions: dict[str, tuple[tuple, JapanMqttSession]] = {}

    def _mqtt_for_vehicle(self, token: Token, vehicle: Vehicle) -> JapanMqttSession:
        """Reuse a subscribed Japan session only while its authentication is current."""
        if (
            not isinstance(token.mqtt_registration_uuid, str)
            or not token.mqtt_registration_uuid
        ):
            raise AuthenticationError(
                "Reauthenticate Japan account to register MQTT support"
            )
        self._command_vehicle_id(vehicle)
        identity = (token.access_token, token.device_id, token.mqtt_registration_uuid)
        existing = self._mqtt_sessions.get(vehicle.id)
        if existing is not None and (
            existing[0] != identity or existing[1].closed is True
        ):
            existing[1].close()
            existing = None
        if existing is None:
            helper = JapanMqttSession(
                token,
                vehicle.id,
                token.mqtt_registration_uuid,
                lambda: self._vehicle_headers(token, vehicle),
            )
            self._mqtt_sessions[vehicle.id] = (identity, helper)
            return helper
        return existing[1]

    def get_local_diagnostics(self) -> dict:
        """Return bounded protocol counters without requests or private data."""
        sessions = list(self._mqtt_sessions.values())[:10]
        return {
            "mqtt_sessions": [helper.diagnostics for _, helper in sessions],
            "svm_images_cached": len(getattr(self, "_japan_svm_cache", {})),
            "last_command": dict(getattr(self, "_japan_command_diagnostics", {})),
            "last_camera_capture": self.svm_diagnostics,
        }

    def close(self) -> None:
        """Release Japan MQTT and HTTP sessions on integration unload."""
        for _, helper in self._mqtt_sessions.values():
            helper.close()
        self._mqtt_sessions.clear()
        self.session.close()

    def force_refresh_vehicle_state(self, token: Token, vehicle: Vehicle) -> None:
        """Wait for correlated completion and newer telemetry, never just an ACK."""
        self._command_vehicle_id(vehicle)
        headers = self._control_headers(token, vehicle)
        helper = self._mqtt_for_vehicle(token, vehicle)
        try:
            self._force_refresh_with_session(token, vehicle, helper, headers)
        finally:
            helper.close()

    def _force_refresh_with_session(self, token, vehicle, helper, headers):
        vehicle_id = self._command_vehicle_id(vehicle)
        helper.prepare()
        # A cached PIN grant can expire while MQTT bootstrap is in progress.
        authorization = self._control_headers(token, vehicle)["Authorization"]
        headers = self._vehicle_headers(token, vehicle, helper)
        headers["Authorization"] = authorization
        deadline = time.monotonic() + self.FORCE_REFRESH_TIMEOUT
        started_at = dt.datetime.now(dt.UTC).replace(microsecond=0)
        result = self._request(
            "GET", f"/api/v2/spa/vehicles/{vehicle_id}/ccs2/carstatus", headers=headers
        )
        action_id = result.get("msgId")
        if not isinstance(action_id, str) or not action_id:
            raise InvalidAPIResponseError(
                "Japan refresh tracking identifier is missing"
            )
        helper.track(action_id)
        status = helper.status(
            action_id, timeout=min(120, max(0, deadline - time.monotonic()))
        )
        if status == ORDER_STATUS.FAILED:
            raise APIError("Japan vehicle refresh failed")
        if status != ORDER_STATUS.SUCCESS:
            raise RequestTimeoutError("Japan vehicle refresh was not confirmed")
        while time.monotonic() < deadline:
            state = helper.latest_state
            candidate = copy.deepcopy(vehicle)
            if isinstance(state, dict):
                try:
                    timestamp = parse_datetime(state.get("Date"), dt.UTC)
                    if (
                        timestamp is not None
                        and timestamp >= started_at
                        and (
                            vehicle.last_updated_at is None
                            or timestamp > vehicle.last_updated_at
                        )
                    ):
                        self._update_vehicle_properties_ccs2(candidate, state)
                        vehicle.__dict__.update(candidate.__dict__)
                        return
                except (KeyError, TypeError, ValueError, AttributeError):
                    pass
            # Some cars publish their fresh data to storage before MQTT state.
            self.update_vehicle_with_cached_state(token, candidate)
            if (
                candidate.last_updated_at is not None
                and candidate.last_updated_at >= started_at
                and (
                    vehicle.last_updated_at is None
                    or candidate.last_updated_at > vehicle.last_updated_at
                )
            ):
                vehicle.__dict__.update(candidate.__dict__)
                return
            time.sleep(min(5, max(0, deadline - time.monotonic())))
        raise RequestTimeoutError("Japan refresh completed without newer telemetry")

    def initialize_vehicle_capabilities(self, vehicle: Vehicle) -> None:
        """Japan options describe each vehicle rather than the entire region."""
        service = self._vehicle_profiles.get(vehicle.id, {}).get("serviceOption", {})
        vehicle.supports_window_control = (
            isinstance(service, dict)
            and type(service.get("windowControlOption")) is int
            and service.get("windowControlOption") == 1
        )
        vehicle.supports_valet_mode = (
            isinstance(service, dict)
            and type(service.get("remoteValetActOption")) is int
            and service.get("remoteValetActOption") == 1
        )
        options = self._vehicle_profiles.get(vehicle.id, {}).get("option", {})
        front_only = (
            isinstance(options, dict)
            and type(options.get("windowSafetyOption2")) is int
            and options.get("windowSafetyOption2") == 4
        )
        vehicle.supported_window_positions = (
            ("front_left", "front_right")
            if front_only
            else ("front_left", "front_right", "back_left", "back_right")
        )
        vehicle.supports_svm = (
            isinstance(options, dict)
            and type(options.get("svmOptionInfo")) is int
            and options.get("svmOptionInfo") == 1
        )
        scale = self._temperature_scale(vehicle)
        vehicle.climate_temperature_range = (
            (scale[2], scale[2] + (scale[1] - scale[0]) * 0.5)
            if scale is not None
            else None
        )

    def _update_vehicle_properties_ccs2(self, vehicle: Vehicle, state: dict) -> None:
        """Map logical driver/passenger telemetry to physical Japan positions."""
        ApiImplType1._update_vehicle_properties_ccs2(self, vehicle, state)
        options = self._vehicle_profiles.get(vehicle.id, {}).get("option", {})
        location = options.get("drvSeatLoc") if isinstance(options, dict) else None
        for left, right in (
            ("front_left_seat_status", "front_right_seat_status"),
            ("front_left_door_is_open", "front_right_door_is_open"),
            ("front_left_door_is_locked", "front_right_door_is_locked"),
            ("front_left_window_is_open", "front_right_window_is_open"),
        ):
            if location == "R":
                left_value, right_value = (
                    getattr(vehicle, left),
                    getattr(vehicle, right),
                )
                setattr(vehicle, left, right_value)
                setattr(vehicle, right, left_value)
            elif location != "L":
                setattr(vehicle, left, None)
                setattr(vehicle, right, None)

    def _stamp(self) -> str:
        epoch = str(int(time.time()))
        if len(epoch) != 10:
            raise APIError("Japan stamp requires a supported ten-digit epoch")
        plain = f"{self.APP_ID}:{epoch}".encode()
        return base64.b64encode(
            bytes(a ^ b for a, b in zip(self._STAMP_STREAM, plain, strict=True))
        ).decode()

    def _headers(self, token: Token | None = None) -> dict:
        headers = {
            "ccsp-service-id": self.SERVICE_ID,
            "ccsp-application-id": self.APP_ID,
            "offset": "9",
            "Stamp": self._stamp(),
        }
        if token is not None:
            headers["Authorization"] = token.access_token
            headers["ccsp-device-id"] = token.device_id
        return headers

    def _vehicle_headers(self, token: Token, vehicle: Vehicle, helper=None) -> dict:
        """Add only known protocol and authenticated, prepared broker context."""
        headers = self._headers(token)
        protocol = vehicle.ccu_ccs2_protocol_support
        if protocol is not None:
            if type(protocol) is not int or protocol < 0:
                raise InvalidAPIResponseError(
                    "Japan vehicle status protocol is invalid"
                )
            headers["ccuCCS2ProtocolSupport"] = str(protocol)
        if helper is None:
            cached = self._mqtt_sessions.get(vehicle.id)
            identity = (
                token.access_token,
                token.device_id,
                token.mqtt_registration_uuid,
            )
            if cached is not None and cached[0] == identity:
                helper = cached[1]
        context = getattr(helper, "request_context", {})
        if isinstance(context, dict):
            for key in ("clientId", "vehicleId"):
                value = context.get(key)
                if isinstance(value, str) and re.fullmatch(
                    r"[A-Za-z0-9_.:-]{1,256}", value
                ):
                    headers[key] = value
        return headers

    _KNOWN_ERRORS: ClassVar[dict[str, tuple[type[Exception], str]]] = {
        "4002": (APIError, "invalid request"),
        "4003": (APIError, "invalid value"),
        "4004": (DuplicateRequestError, "duplicate request"),
        "4006": (APIError, "account locked"),
        "4016": (APIError, "account migration required"),
        "4081": (RequestTimeoutError, "request expired"),
        "5031": (ServiceTemporaryUnavailable, "remote control unavailable"),
        "5091": (RateLimitingError, "request limit reached"),
        "5911": (NoDataFound, "no data found"),
        "5921": (NoDataFound, "no data found"),
        "7501": (AuthenticationError, "authentication expired"),
        "4292": (RateLimitingError, "surround-view capture limit reached"),
        "7779": (ServiceTemporaryUnavailable, "surround-view unavailable"),
        "7780": (APIError, "surround-view camera error"),
        "7781": (APIError, "surround-view blocked by emergency lights"),
        "7782": (APIError, "surround-view blocked by low battery"),
        "5016": (APIError, "vehicle has not uploaded data"),
        "4101": (APIError, "vehicle is not registered"),
        "4140": (APIError, "vehicle profile is not owned by this account"),
        "4008": (APIError, "vehicle system is updating"),
        "4901": (APIError, "vehicle is immobilized"),
        "5311": (APIError, "stolen-vehicle remote control unavailable"),
        "5152": (APIError, "no GPS data found"),
        "4011": (APIError, "verification number is incorrect"),
        "4291": (APIError, "authentication already processed"),
    }

    @classmethod
    def _diagnostic(cls, http_status, result: dict) -> str:
        details = []
        if type(http_status) is int and 100 <= http_status <= 599:
            details.append(f"HTTP {http_status}")
        code = str(
            result.get("resCode", result.get("resultCode", result.get("errCode", "")))
        )
        known = cls._KNOWN_ERRORS.get(code)
        if known is not None:
            details.append(f"code {code}: {known[1]}")
        return " (" + ", ".join(details) + ")" if details else ""

    def _request(self, method: str, path: str, *, json_response=True, **kwargs):
        """Expose HTTP status and allowlisted labels, never response text or URLs."""
        kwargs.setdefault("allow_redirects", False)
        try:
            response = self.session.request(method, self.BASE_URL + path, **kwargs)
        except (requests.exceptions.Timeout, RequestTimeoutError):
            raise RequestTimeoutError("Japan API request timed out") from None
        except requests.exceptions.RequestException:
            raise APIError("Japan API connection failed") from None
        status = response.status_code
        if not json_response and (200 <= status < 300 or 300 <= status < 400):
            return response
        try:
            result = response.json()
        except ValueError:
            if 200 <= status < 300:
                raise InvalidAPIResponseError(
                    f"Japan API returned invalid JSON (HTTP {status})"
                ) from None
            result = {}
        if not isinstance(result, dict):
            if 200 <= status < 300:
                raise InvalidAPIResponseError(
                    f"Japan API returned an invalid object (HTTP {status})"
                )
            result = {}
        diagnostic = self._diagnostic(status, result)
        if status in (401, 403):
            raise AuthenticationError("Japan authentication was rejected" + diagnostic)
        if status == 429:
            raise RateLimitingError("Japan API rate limit reached" + diagnostic)
        if status >= 500:
            raise ServiceTemporaryUnavailable("Japan API is unavailable" + diagnostic)
        self._check_errors(result, http_status=status, path=path)
        if not 200 <= status < 300:
            raise APIError("Japan API request was rejected" + diagnostic)
        return result

    @classmethod
    def _check_errors(cls, result: dict, *, http_status=None, path=None) -> None:
        code = str(
            result.get("resCode", result.get("resultCode", result.get("errCode", "")))
        )
        diagnostic = cls._diagnostic(http_status, result)
        if path == "/api/v1/user/signin" and code in ("4003", "4006", "4016"):
            raise AuthenticationError("Japan sign-in was rejected" + diagnostic)
        if path == "/api/v1/user/pin" and code == "4003":
            raise AuthenticationError("Japan PIN was rejected" + diagnostic)
        if result.get("error") in ("invalid_grant", "invalid_token"):
            raise AuthenticationError("Japan token grant was rejected" + diagnostic)
        known = cls._KNOWN_ERRORS.get(code)
        if known is not None:
            raise known[0]("Japan API rejected the request" + diagnostic)
        if (
            result.get("retCode", result.get("resultType")) == "F"
            or (code and code != "0000")
            or result.get("error")
        ):
            raise APIError("Japan API rejected the request" + diagnostic)

    @staticmethod
    def _payload(result: dict) -> dict:
        payload = result.get("resMsg", result.get("responseEntity"))
        if not isinstance(payload, dict):
            raise InvalidAPIResponseError("Japan API response payload is missing")
        return payload

    def _exchange_token(self, grant: dict, username, password, device_id, pin) -> Token:
        headers = self._headers()
        headers["Authorization"] = self.BASIC_AUTHORIZATION
        result = self._request(
            "POST",
            "/api/v1/user/oauth2/token",
            headers=headers,
            data={
                "client_id": self.SERVICE_ID,
                "redirect_uri": self.REDIRECT_URI,
                **grant,
            },
        )
        try:
            access = result["access_token"]
            token_type = result["token_type"]
            expiry = result["expires_in"]
            if isinstance(expiry, bool) or not re.fullmatch(r"[0-9]+", str(expiry)):
                raise ValueError
            lifetime = int(expiry)
            if not isinstance(access, str) or not access or lifetime <= 0:
                raise ValueError
            if token_type != "Bearer":
                raise ValueError
            refresh = result.get("refresh_token", grant.get("refresh_token"))
            if not isinstance(refresh, str) or not refresh:
                raise ValueError
            valid_until = dt.datetime.now(dt.UTC) + dt.timedelta(seconds=lifetime)
        except (KeyError, TypeError, ValueError, OverflowError):
            raise InvalidAPIResponseError(
                "Japan token response is incomplete"
            ) from None
        return Token(
            username=username,
            password=password,
            access_token=f"{token_type} {access}",
            refresh_token=refresh,
            device_id=device_id,
            pin=pin,
            valid_until=valid_until,
        )

    def login(self, username: str, password: str, pin: str | None = None) -> Token:
        """Establish cookies, sign in, exchange the code, and register a device."""
        response = self._request(
            "GET",
            "/api/v1/user/oauth2/authorize",
            json_response=False,
            allow_redirects=False,
            params={
                "response_type": "code",
                "client_id": self.SERVICE_ID,
                "redirect_uri": self.REDIRECT_URI,
                "lang": "ja",
            },
        )
        for _ in range(5):
            if not 300 <= response.status_code < 400:
                break
            target = urljoin(response.url, response.headers.get("Location", ""))
            parsed = urlparse(target)
            expected = urlparse(self.BASE_URL)
            if (parsed.scheme, parsed.netloc) != (expected.scheme, expected.netloc):
                raise AuthenticationError(
                    "Japan login redirected to an unexpected host"
                )
            response = self._request(
                "GET",
                parsed.path + ("?" + parsed.query if parsed.query else ""),
                json_response=False,
                allow_redirects=False,
            )
        else:
            raise AuthenticationError("Japan login exceeded its redirect limit")
        self._request(
            "POST", "/api/v1/user/language", json_response=False, json={"lang": "ja"}
        )
        result = self._request(
            "POST",
            "/api/v1/user/signin",
            json={"email": username, "password": password, "mobileNum": ""},
        )
        redirect = result.get("redirectUrl")
        if not isinstance(redirect, str):
            raise ConsentRequiredError(
                "Complete Japan account setup in the official app"
            )
        parsed = urlparse(redirect)
        expected = urlparse(self.REDIRECT_URI)
        code = parse_qs(parsed.query).get("code", [])
        if (
            (parsed.scheme, parsed.netloc, parsed.path)
            != (
                expected.scheme,
                expected.netloc,
                expected.path,
            )
            or len(code) != 1
            or not code[0]
        ):
            raise AuthenticationError(
                "Japan login returned no valid authorization code"
            )
        token = self._exchange_token(
            {"grant_type": "authorization_code", "code": code[0]},
            username,
            password,
            None,
            pin,
        )
        headers = self._headers()
        # The app uses blank placeholders; requests rejects whitespace header
        # values. This fresh session has no default authorization/device header.
        headers.update({"Authorization": "", "ccsp-device-id": ""})
        registration_uuid = str(uuid.uuid4())
        result = self._request(
            "POST",
            "/api/v1/spa/notifications/register",
            headers=headers,
            json={
                "uuid": registration_uuid,
                "pushRegId": uuid.uuid4().hex,
                "pushType": "GCM",
            },
        )
        token.device_id = self._payload(result).get("deviceId")
        if not isinstance(token.device_id, str) or not token.device_id:
            raise InvalidAPIResponseError("Japan device registration was incomplete")
        token.mqtt_registration_uuid = registration_uuid
        return token

    def refresh_access_token(self, token: Token) -> Token:
        """Use the refresh grant; preserve the registered device identity."""
        if not token.refresh_token:
            raise AuthenticationError("Japan refresh token is missing; sign in again")
        refreshed = self._exchange_token(
            {"grant_type": "refresh_token", "refresh_token": token.refresh_token},
            token.username,
            token.password,
            token.device_id,
            token.pin,
        )
        refreshed.mqtt_registration_uuid = token.mqtt_registration_uuid
        return refreshed

    def get_vehicles(self, token: Token) -> list[Vehicle]:
        """Discover vehicles and retrieve profiles to select the status protocol."""
        result = self._request(
            "GET", "/api/v1/spa/vehicles", headers=self._headers(token)
        )
        entries = self._payload(result).get("vehicles")
        if not isinstance(entries, list):
            raise InvalidAPIResponseError("Japan vehicle list is missing")
        vehicles = []
        engine_types = {
            "GN": ENGINE_TYPES.ICE,
            "EV": ENGINE_TYPES.EV,
            "PHEV": ENGINE_TYPES.PHEV,
            "PE": ENGINE_TYPES.PHEV,
            "HV": ENGINE_TYPES.HEV,
        }
        for entry in entries:
            if not isinstance(entry, dict) or not isinstance(
                entry.get("vehicleId"), str
            ):
                raise InvalidAPIResponseError("Japan vehicle identifier is missing")
            vehicle_id = entry["vehicleId"]
            if not re.fullmatch(r"[A-Za-z0-9_-]+", vehicle_id):
                raise InvalidAPIResponseError("Japan vehicle identifier is invalid")
            result = self._request(
                "GET",
                f"/api/v1/spa/vehicles/{vehicle_id}/profile",
                headers=self._vehicle_headers(token, Vehicle(id=vehicle_id)),
            )
            profiles = self._payload(result).get("vinInfo")
            if not isinstance(profiles, list) or len(profiles) != 1:
                raise InvalidAPIResponseError("Japan vehicle profile is incomplete")
            basic = profiles[0].get("basic") if isinstance(profiles[0], dict) else None
            if not isinstance(basic, dict):
                raise InvalidAPIResponseError("Japan vehicle profile is incomplete")
            protocol = basic.get("ccuCCS2ProtocolSupport")
            if type(protocol) is not int or protocol < 0:
                raise InvalidAPIResponseError(
                    "Japan vehicle status protocol is missing"
                )
            self._vehicle_profiles[vehicle_id] = profiles[0]
            vehicles.append(
                Vehicle(
                    id=vehicle_id,
                    name=entry.get("nickname") or entry.get("vehicleName"),
                    model=basic.get("modelName") or entry.get("vehicleName"),
                    registration_date=entry.get("regDate"),
                    VIN=basic.get("vin"),
                    timezone=self.data_timezone,
                    engine_type=engine_types.get(entry.get("type")),
                    ccu_ccs2_protocol_support=protocol,
                )
            )
        for vehicle in vehicles:
            self.initialize_vehicle_capabilities(vehicle)
        return vehicles

    def update_vehicle_with_cached_state(self, token: Token, vehicle: Vehicle) -> None:
        """Read stored status, optional parking data, and daily energy totals."""
        protocol = vehicle.ccu_ccs2_protocol_support
        if type(protocol) is not int or protocol < 0:
            raise InvalidAPIResponseError("Japan vehicle status protocol is missing")
        if not isinstance(vehicle.id, str) or not re.fullmatch(
            r"[A-Za-z0-9_-]+", vehicle.id
        ):
            raise InvalidAPIResponseError("Japan vehicle identifier is invalid")
        endpoint = "ccs2/carstatus/latest" if protocol else "status/latest"
        headers = self._vehicle_headers(token, vehicle)
        headers["ccuCCS2ProtocolSupport"] = str(protocol)
        payload = self._payload(
            self._request(
                "GET", f"/api/v1/spa/vehicles/{vehicle.id}/{endpoint}", headers=headers
            )
        )
        try:
            parsed_vehicle = copy.deepcopy(vehicle)
            if protocol:
                state = payload["state"]["Vehicle"]
                if not isinstance(state, dict):
                    raise TypeError
                if not state.get("Date"):
                    raise ValueError
                parse_datetime(state["Date"], dt.UTC)
                self._update_vehicle_properties_ccs2(parsed_vehicle, state)
            else:
                info = payload["vehicleStatusInfo"]
                state = {
                    "status": info["vehicleStatus"],
                    "vehicleLocation": info.get("vehicleLocation"),
                }
                if not isinstance(state["status"], dict):
                    raise TypeError
                if not state["status"].get("time"):
                    raise ValueError
                parse_datetime(state["status"]["time"], self.data_timezone)
                temperature = self._legacy_temperature(
                    vehicle, state["status"].get("airTemp")
                )
                state = copy.deepcopy(state)
                state["status"].pop("airTemp", None)
                self._update_vehicle_properties(parsed_vehicle, state)
                profile_options = self._vehicle_profiles.get(vehicle.id, {}).get(
                    "option", {}
                )
                seat_location = (
                    profile_options.get("drvSeatLoc")
                    if isinstance(profile_options, dict)
                    else None
                )
                seats = get_child_value(state, "status.seatHeaterVentState")
                if isinstance(seats, dict) and seat_location in ("L", "R"):
                    for physical, logical in (
                        (
                            "front_left_seat_status",
                            "drvSeatHeatState"
                            if seat_location == "L"
                            else "astSeatHeatState",
                        ),
                        (
                            "front_right_seat_status",
                            "astSeatHeatState"
                            if seat_location == "L"
                            else "drvSeatHeatState",
                        ),
                    ):
                        if (
                            getattr(parsed_vehicle, physical) is None
                            and logical in seats
                        ):
                            setattr(
                                parsed_vehicle,
                                physical,
                                SEAT_STATUS.get(seats[logical]),
                            )
                parsed_vehicle.air_temperature = (
                    temperature,
                    "°C" if temperature is not None else None,
                )
        except (KeyError, TypeError, ValueError, AttributeError):
            raise InvalidAPIResponseError("Japan cached status is incomplete") from None
        # Supplement only absent parking data and limit history reads to daily.
        # Optional malformed data cannot invalidate otherwise valid status.
        if (
            parsed_vehicle.location_latitude is None
            or parsed_vehicle.location_longitude is None
        ) and time.monotonic() - self._location_missing_at.get(
            vehicle.id, -3600
        ) >= 3600:
            try:
                self.update_vehicle_with_cached_location(token, parsed_vehicle)
            except (NoDataFound, InvalidAPIResponseError):
                self._location_missing_at[vehicle.id] = time.monotonic()
        if (
            vehicle.engine_type in (ENGINE_TYPES.EV, ENGINE_TYPES.PHEV)
            and time.monotonic() - self._history_updated_at.get(vehicle.id, -86400)
            >= 86400
        ):
            try:
                self.update_vehicle_with_driving_info(token, parsed_vehicle)
            except (NoDataFound, InvalidAPIResponseError):
                pass
            self._history_updated_at[vehicle.id] = time.monotonic()
        if parsed_vehicle.engine_type in (ENGINE_TYPES.EV, ENGINE_TYPES.PHEV) and (
            parsed_vehicle.ev_charge_limits_ac is None
            or parsed_vehicle.ev_charge_limits_dc is None
        ):
            cached_targets = self._charge_target_cache.get(vehicle.id)
            if (
                cached_targets is not None
                and time.monotonic() - cached_targets[0] < 3600
            ):
                targets = cached_targets[1]
                parsed_vehicle.ev_charge_limits_ac = targets.get(1, targets.get(2))
                parsed_vehicle.ev_charge_limits_dc = targets.get(0)
            else:
                try:
                    self.update_vehicle_with_charge_targets(token, parsed_vehicle)
                except (NoDataFound, InvalidAPIResponseError):
                    self._charge_target_cache[vehicle.id] = (time.monotonic(), {})
        vehicle.__dict__.update(parsed_vehicle.__dict__)

    def update_vehicle_with_charge_targets(
        self, token: Token, vehicle: Vehicle
    ) -> None:
        """Read stored AC/DC targets without waking the vehicle."""
        vehicle_id = self._command_vehicle_id(vehicle)
        payload = self._payload(
            self._request(
                "GET",
                f"/api/v1/spa/vehicles/{vehicle_id}/charge/target",
                headers=self._vehicle_headers(token, vehicle),
            )
        )
        entries = payload.get("targetSOClist")
        if not isinstance(entries, list):
            raise InvalidAPIResponseError("Japan charge target response is incomplete")
        targets = {}
        for entry in entries:
            if not isinstance(entry, dict):
                raise InvalidAPIResponseError(
                    "Japan charge target response is incomplete"
                )
            plug, level = entry.get("plugType"), entry.get("targetSOClevel")
            if (
                type(plug) is not int
                or plug not in (0, 1, 2)
                or plug in targets
                or plug in (1, 2)
                and any(kind in targets for kind in (1, 2))
                or type(level) is not int
                or not 0 <= level <= 100
            ):
                raise InvalidAPIResponseError("Japan charge target response is invalid")
            targets[plug] = level
        self._charge_target_cache[vehicle_id] = (time.monotonic(), targets)
        vehicle.ev_charge_limits_ac = targets.get(1, targets.get(2))
        vehicle.ev_charge_limits_dc = targets.get(0)

    def _legacy_temperature(self, vehicle: Vehicle, air_temp) -> float | None:
        """Decode profile-dependent Japan temperature codes, leaving LO/HI unknown."""
        if not isinstance(air_temp, dict) or air_temp.get("unit", 0) != 0:
            return None
        scale = self._temperature_scale(vehicle, air_temp.get("hvacTempType"))
        if scale is None:
            return None
        first_code, last_code, minimum = scale
        value = air_temp.get("value")
        if not isinstance(value, str) or not re.fullmatch(r"[0-9A-Fa-f]{2}H?", value):
            return None
        code = int(value.removesuffix("H"), 16)
        return (
            minimum + (code - first_code) * 0.5
            if first_code <= code <= last_code
            else None
        )

    def _temperature_scale(self, vehicle: Vehicle, hvac_type=None):
        """Return only a scale established by profile or reported HVAC type."""
        options = self._vehicle_profiles.get(vehicle.id, {}).get("option", {})
        if not isinstance(options, dict):
            return None
        range_option = options.get("climateTempRangeOption")
        if hvac_type is None:
            hvac_type = options.get("hvacTempType")
        if isinstance(range_option, bool) or isinstance(hvac_type, bool):
            return None
        if range_option == 0 or hvac_type in (1, 2) and range_option not in (0, 1):
            first_code, last_code, minimum = 0x06, 0x1A, 17.0
        elif range_option == 1:
            first_code, last_code, minimum = 0x03, 0x1F, 15.5
        elif hvac_type == 4:
            first_code, last_code, minimum = 0x02, 0x20, 15.0
        else:
            return None
        return first_code, last_code, minimum

    def update_vehicle_with_cached_location(
        self, token: Token, vehicle: Vehicle
    ) -> None:
        """Read the stored parking position without contacting the vehicle."""
        if not isinstance(vehicle.id, str) or not re.fullmatch(
            r"[A-Za-z0-9_-]+", vehicle.id
        ):
            raise InvalidAPIResponseError("Japan vehicle identifier is invalid")
        payload = self._payload(
            self._request(
                "GET",
                f"/api/v1/spa/vehicles/{vehicle.id}/location/park",
                headers=self._vehicle_headers(token, vehicle),
            )
        )
        location = payload.get("gpsDetail", payload)
        try:
            if not isinstance(location, dict):
                raise TypeError
            coord = location["coord"]
            latitude, longitude = coord["lat"], coord["lon"]
            if isinstance(latitude, bool) or isinstance(longitude, bool):
                raise TypeError
            latitude, longitude = float(latitude), float(longitude)
            if (
                not math.isfinite(latitude)
                or not math.isfinite(longitude)
                or not -90 <= latitude <= 90
                or not -180 <= longitude <= 180
            ):
                raise ValueError
            timestamp = parse_datetime(location["time"], self.data_timezone)
            if timestamp is None:
                raise ValueError
        except (KeyError, TypeError, ValueError):
            raise InvalidAPIResponseError(
                "Japan cached location is incomplete"
            ) from None
        vehicle.location = (latitude, longitude, timestamp)

    def update_vehicle_with_driving_info(self, token: Token, vehicle: Vehicle) -> None:
        """Read all-time stored EV energy history (periodTarget 0)."""
        if not isinstance(vehicle.id, str) or not re.fullmatch(
            r"[A-Za-z0-9_-]+", vehicle.id
        ):
            raise InvalidAPIResponseError("Japan vehicle identifier is invalid")
        payload = self._payload(
            self._request(
                "POST",
                f"/api/v1/spa/vehicles/{vehicle.id}/drvhistory",
                headers=self._vehicle_headers(token, vehicle),
                json={"periodTarget": 0},
            )
        )
        entries = payload.get("drivingInfo")
        if not isinstance(entries, list):
            raise InvalidAPIResponseError("Japan driving history is incomplete")
        all_time = next(
            (
                item
                for item in entries
                if isinstance(item, dict) and item.get("drivingPeriod") == 0
            ),
            None,
        )
        if all_time is None:
            raise NoDataFound("Japan all-time driving history is unavailable")

        def metric(key):
            value = all_time.get(key)
            if value is None:
                return None
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value < 0
            ):
                raise InvalidAPIResponseError("Japan driving history metric is invalid")
            return value

        consumed, regenerated = metric("totalPwrCsp"), metric("regenPwr")
        daily = payload.get("drivingInfoDetail", [])
        if not isinstance(daily, list):
            raise InvalidAPIResponseError("Japan daily energy history is incomplete")
        daily_stats = []
        for day in daily:
            if not isinstance(day, dict):
                raise InvalidAPIResponseError(
                    "Japan daily energy history is incomplete"
                )
            try:
                day_date = dt.datetime.strptime(day["drivingDate"], "%Y%m%d").replace(
                    tzinfo=self.data_timezone
                )
            except (KeyError, TypeError, ValueError):
                raise InvalidAPIResponseError(
                    "Japan daily energy date is invalid"
                ) from None
            for key in (
                "totalPwrCsp",
                "motorPwrCsp",
                "climatePwrCsp",
                "eDPwrCsp",
                "batteryMgPwrCsp",
                "regenPwr",
                "calculativeOdo",
            ):
                value = day.get(key)
                if value is not None and (
                    isinstance(value, bool)
                    or not isinstance(value, (int, float))
                    or not math.isfinite(value)
                    or value < 0
                ):
                    raise InvalidAPIResponseError(
                        "Japan daily energy metric is invalid"
                    )
            daily_stats.append(
                DailyDrivingStats(
                    date=day_date,
                    total_consumed=day.get("totalPwrCsp"),
                    engine_consumption=day.get("motorPwrCsp"),
                    climate_consumption=day.get("climatePwrCsp"),
                    onboard_electronics_consumption=day.get("eDPwrCsp"),
                    battery_care_consumption=day.get("batteryMgPwrCsp"),
                    regenerated_energy=day.get("regenPwr"),
                    distance=day.get("calculativeOdo"),
                    distance_unit="km",
                )
            )
        vehicle.total_power_consumed = consumed
        vehicle.total_power_regenerated = regenerated
        vehicle.daily_stats = daily_stats
