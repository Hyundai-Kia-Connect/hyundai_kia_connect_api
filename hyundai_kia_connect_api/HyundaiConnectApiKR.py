"""Hyundai Korea implementation for the unified MyHyundai app."""

# pylint:disable=missing-class-docstring,missing-function-docstring,invalid-name,logging-fstring-interpolation,broad-except

import datetime as dt
import logging
import time
import uuid
from typing import Any, ClassVar
from urllib.parse import parse_qs, urlencode, urlparse

import requests

from .ApiImpl import ClimateRequestOptions
from .const import DOMAIN, ENGINE_TYPES, VEHICLE_LOCK_ACTION
from .exceptions import (
    APIError,
    AuthenticationError,
    PINMissingError,
    ServiceTemporaryUnavailable,
)
from .gspa import create_tsid
from .GspaApiEU import USER_AGENT_OK_HTTP
from .HyundaiCciApiEU import HyundaiCciApiEU
from .Token import Token
from .utils import get_child_value, parse_datetime
from .Vehicle import Vehicle

_LOGGER = logging.getLogger(__name__)


class HyundaiConnectApiKR(HyundaiCciApiEU):
    """Hyundai Korea CCI login and domestic connected-car API.

    The Korean MyHyundai app shares CCI authentication with Hyundai OneApp,
    but vehicle state and controls use the Korean CCAPI service. These calls
    do not use the GSPA X-Stamp used by the European implementation.
    """

    data_timezone = dt.timezone(dt.timedelta(hours=9))
    supports_valet_mode = False
    SUPPORTED_LANGUAGES = ("ko", "en")

    ONEAPP_CLIENT_ID = "11769a37-9a46-48c8-82f4-24a2a11c1337"
    ONEAPP_REDIRECT_URI = "https://oneapp.hyundai.com/redirect"
    CCI_API_URL = "https://cci-api-kr.hyundai.com"
    CCI_PACKAGE_ID = "com.hyundai.oneapp.kr"
    GSPA_BASE_URL = "https://prd.kr-ccapi.hyundai.com/"
    LOGIN_FORM_HOST = "https://idpconnect-kr.hyundai.com"
    CIPHER_BRAND = "hyundai"
    REQUEST_ID_HEADER = "X-Request-Id"
    DEVICE_ID_HEADER = "X-Device-Id"
    CCSP_SERVICE_ID = "25fa8900-60b0-4f5d-802b-04c7168f64ea"
    CCSP_APPLICATION_ID = "a8e416c3-5832-4f70-9f9d-7ecbfc8d96ca"
    LOGIN_COUNTRY = ""
    LOGIN_LANGUAGE = None
    LOGIN_STATE = "hmgoneapp"
    CCI_REFRESH_SEND_AUTH_HEADERS = False
    CCI_REFRESH_SEND_ID_TOKEN = False
    # Keep this aligned with the production loginUrl shipped in the Korean
    # MyHyundai app. In particular, ``offline`` requests the renewable token
    # set needed to restore a session without another browser login.
    LOGIN_SCOPE = (
        "account.token.transfer account.id.generate account.puid.userinfos "
        "account.userinfo read puid name email mobileNum birthdate lang country "
        "signUpDate offline certProfile"
    )

    STATUS_PATH = "api/v1/apps/spa/tmc_new/ccsp"
    REMOTE_PATH = "apps/spa/tmc_new"

    ENGINE_SERVICE_NO = "F52"
    CLIMATE_SERVICE_NO = "E28"
    SEAT_CLIMATE_STATES: ClassVar[dict[int, frozenset[int]]] = {
        0: frozenset(),
        1: frozenset((2, 6, 7)),
        2: frozenset((2, 6, 7, 8)),
        3: frozenset((2, 3, 4)),
        4: frozenset((2, 3, 4, 5)),
        5: frozenset((2, 3, 4, 6, 7)),
        6: frozenset((2, 3, 4, 5, 6, 7, 8)),
        7: frozenset(),
        8: frozenset((2, 8)),
        9: frozenset((2, 5, 8)),
    }
    SEAT_CLIMATE_FIELDS: ClassVar[tuple[tuple[str, str, str], ...]] = (
        (
            "drvSeatHeatState",
            "front_left_seat",
            "front_left_seat_climate_capability",
        ),
        (
            "astSeatHeatState",
            "front_right_seat",
            "front_right_seat_climate_capability",
        ),
        (
            "rlSeatHeatState",
            "rear_left_seat",
            "rear_left_seat_climate_capability",
        ),
        (
            "rrSeatHeatState",
            "rear_right_seat",
            "rear_right_seat_climate_capability",
        ),
    )

    def __init__(self, region: int, brand: int, language: str) -> None:
        """Initialize the Korea API with MyHyundai Android client metadata."""
        super().__init__(region, brand, language)
        self._cci_client_version = "1.6.0"
        self._cci_client_os_version = "16"
        self._cci_notification_provider = "FCM"

    def get_authorization_url(self) -> str:
        """Return the Pleos URL used for interactive browser login."""
        query = urlencode(
            {
                "response_type": "code",
                "client_id": self.ONEAPP_CLIENT_ID,
                "redirect_uri": self.ONEAPP_REDIRECT_URI,
                "state": self.LOGIN_STATE,
                "scope": self.LOGIN_SCOPE,
            }
        )
        return f"{self.LOGIN_FORM_HOST}/auth/api/v2/user/oauth2/authorize?{query}"

    def login_with_redirect_url(
        self,
        redirect_url: str,
        pin: str | None = None,
        device_id: str | None = None,
    ) -> Token:
        """Complete an interactive Pleos login from the final redirect URL."""
        parsed = urlparse(redirect_url)
        expected = urlparse(self.ONEAPP_REDIRECT_URI)
        if (parsed.scheme, parsed.netloc, parsed.path) != (
            expected.scheme,
            expected.netloc,
            expected.path,
        ):
            raise AuthenticationError("Unexpected Pleos login redirect URL")

        query = parse_qs(parsed.query)
        if query.get("state", [""])[0] != self.LOGIN_STATE:
            raise AuthenticationError("Pleos login redirect had an invalid state")
        auth_code = query.get("code", [""])[0]
        if not auth_code:
            error = query.get("error_description", query.get("error", ["unknown"]))[0]
            raise AuthenticationError(f"Pleos login did not return a code: {error}")
        return self.login_with_authorization_code(auth_code, pin, device_id)

    def login_with_authorization_code(
        self,
        auth_code: str,
        pin: str | None = None,
        device_id: str | None = None,
    ) -> Token:
        """Exchange a Pleos browser authorization code for a package token."""
        if not auth_code:
            raise AuthenticationError("Pleos authorization code is empty")
        device_id = device_id or str(uuid.uuid4())
        cci = self._exchange_auth_code_for_cci_tokens(device_id, auth_code)
        cci_access_token = cci.get("accessToken", "")
        exchangeable_token = cci.get("exchangeableAccessToken", "")
        non_ccs_token = cci.get("nonCcsToken", "")
        ccs_token, valid_until = self._exchange_ccs_token(
            device_id, cci_access_token, non_ccs_token, exchangeable_token
        )
        token = Token(
            access_token=f"Bearer {ccs_token}",
            refresh_token=cci.get("refreshToken", ""),
            device_id=device_id,
            valid_until=valid_until,
            pin=pin,
            cci_access_token=cci_access_token,
            exchangeable_token=exchangeable_token,
            exchangeable_refresh_token=cci.get("exchangeableRefreshToken", ""),
            non_ccs_token=non_ccs_token,
            non_ccs_refresh_token=cci.get("nonCcsRefreshToken", ""),
            id_token=cci.get("idToken", ""),
        )
        self._register_device(token)
        self._fetch_user_id(token)
        return token

    def refresh_access_token(self, token: Token) -> Token:
        """Renew a Pleos session without falling back to password login."""
        if token.cci_access_token or token.non_ccs_token:
            return self._refresh_cci_token(token)
        raise AuthenticationError(
            "Hyundai Korea Pleos session cannot be renewed; sign in again"
        )

    def _get_cci_headers(
        self,
        device_id: str,
        cci_access_token: str | None = None,
        non_ccs_token: str | None = None,
        exchangeable_token: str | None = None,
        content_type: str | None = None,
    ) -> dict[str, Any]:
        """Build CCI headers that identify the Korea Android application."""
        headers = super()._get_cci_headers(
            device_id,
            cci_access_token=cci_access_token,
            non_ccs_token=non_ccs_token,
            exchangeable_token=exchangeable_token,
            content_type=content_type,
        )
        headers.update(
            {
                "client-os-code": "AOS",
                "client-device-model": "Android",
            }
        )
        return headers

    def _register_device(self, token: Token) -> None:
        """Skip push registration because it requires a genuine FCM token."""

    def _fetch_user_id(self, token: Token) -> None:
        """Fetch and persist the domestic API's connected-car customer ID."""
        super()._fetch_user_id(token)
        if token.cc_id:
            return
        url = self.CCI_DOMAIN_API_URL + "v1/ccsp/me"
        headers = self._get_cci_headers(
            token.device_id or "",
            cci_access_token=token.cci_access_token,
            non_ccs_token=token.non_ccs_token,
            exchangeable_token=token.exchangeable_token,
        )
        try:
            response = requests.get(url, headers=headers, timeout=(5, 30))
            if response.status_code == 200:
                data = response.json()
                cc_id = data.get("id")
                if isinstance(cc_id, str) and cc_id:
                    token.cc_id = cc_id
            else:
                _LOGGER.debug(
                    "%s - CCI profile request failed: HTTP %s",
                    DOMAIN,
                    response.status_code,
                )
        except Exception:
            _LOGGER.debug("%s - CCI profile request failed", DOMAIN)

    def _ensure_cc_id(self, token: Token) -> str:
        """Return the connected-car customer ID, fetching it when absent."""
        if not token.cc_id:
            self._fetch_user_id(token)
        if not token.cc_id:
            raise APIError("Hyundai Korea profile returned no connected-car ID")
        return token.cc_id

    def _ensure_user_id(self, token: Token) -> str:
        """Return the Pleos profile ID, fetching it when absent."""
        if not token.user_id:
            self._fetch_user_id(token)
        if not token.user_id:
            raise APIError("Hyundai Korea profile returned no profile ID")
        return token.user_id

    def _get_authenticated_headers(
        self, token: Token, ccs2_support: int | None = None
    ) -> dict[str, Any]:
        """Build Korean domestic API headers, which intentionally omit X-Stamp."""
        self._validate_ccs_token(token)
        ccs_token = (token.access_token or "").removeprefix("Bearer ").strip()
        headers = {
            "Authorization": f"Bearer {ccs_token}",
            "ccsp-service-id": self.CCSP_SERVICE_ID,
            "ccsp-application-id": self.CCSP_APPLICATION_ID,
            self.REQUEST_ID_HEADER: create_tsid(
                (token.device_id or "").replace("-", "")
            ),
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": USER_AGENT_OK_HTTP,
        }
        if ccs2_support is not None:
            headers["ccuCCS2ProtocolSupport"] = str(ccs2_support)
        return headers

    def _domestic_post(
        self,
        token: Token,
        endpoint: str,
        body: dict[str, Any],
        authorization: str | None = None,
        ccs2_support: int | None = None,
    ) -> dict[str, Any]:
        """Send one authenticated request to the Korea connected-car API."""
        headers = self._get_authenticated_headers(token, ccs2_support)
        if authorization is not None:
            headers["Authorization"] = authorization
        url = f"{self.CCSP_API_URL}/{endpoint.lstrip('/')}"
        try:
            response = requests.post(url, headers=headers, json=body, timeout=(5, 60))
        except requests.RequestException as exc:
            raise ServiceTemporaryUnavailable(
                "Hyundai Korea API temporarily unavailable: network request failed"
            ) from exc
        if response.status_code == 401:
            raise AuthenticationError("Hyundai Korea token expired or invalid")
        try:
            payload: dict[str, Any] = response.json()
        except ValueError as exc:
            if response.status_code >= 500:
                raise ServiceTemporaryUnavailable(
                    "Hyundai Korea API temporarily unavailable: "
                    f"HTTP {response.status_code} with no JSON"
                ) from exc
            raise APIError(
                f"Hyundai Korea API returned HTTP {response.status_code} with no JSON"
            ) from exc
        meta = payload.get("metaInfo", payload)
        ret_code = meta.get("retCode", meta.get("RetCode"))
        if ret_code == "F":
            code = meta.get("resCode") or meta.get("rspCode") or "unknown"
            message = meta.get("message", "request failed")
            error = f"Hyundai Korea API error: {code} {message}"
            if response.status_code >= 500:
                raise ServiceTemporaryUnavailable(error)
            raise APIError(error)
        if response.status_code >= 500:
            raise ServiceTemporaryUnavailable(
                f"Hyundai Korea API temporarily unavailable: HTTP {response.status_code}"
            )
        if response.status_code >= 400:
            raise APIError(f"Hyundai Korea API error: HTTP {response.status_code}")
        data = payload.get("data", payload.get("resMsg", payload))
        return data if isinstance(data, dict) else {}

    def _status_body(self, token: Token, vehicle: Vehicle, service_no: str) -> dict:
        """Build the shared identity fields for a Korea vehicle request."""
        return {
            "CCID": f"{self._ensure_cc_id(token).removesuffix('_BLU')}_BLU",
            "carID": vehicle.id,
            "ServiceNo": service_no,
        }

    def update_vehicle_with_cached_state(self, token: Token, vehicle: Vehicle) -> None:
        """Update a vehicle from MyHyundai Korea's most recent cached status."""
        if not getattr(vehicle, "window_status_capabilities_loaded", False):
            try:
                self.get_vehicle_capabilities(token, vehicle)
            except AuthenticationError:
                raise
            except Exception as exc:  # pylint: disable=broad-except
                _LOGGER.warning(
                    "%s - Korea capability discovery unavailable; continuing "
                    "without window status: %s",
                    DOMAIN,
                    exc,
                )
        data = self._domestic_post(
            token,
            f"{self.STATUS_PATH}/recentcarstatus_ccs2.do",
            self._status_body(token, vehicle, "F39"),
        )
        state = data.get("state", data)
        if isinstance(state, dict) and "Vehicle" in state:
            state = state["Vehicle"]
        if not isinstance(state, dict) or not state:
            raise APIError("Hyundai Korea status response contained no vehicle state")
        self._update_vehicle_properties_ccs2(vehicle, state)
        self._update_korean_window_properties(vehicle, state)

    @staticmethod
    def _update_korean_window_properties(
        vehicle: Vehicle, state: dict[str, Any]
    ) -> None:
        """Interpret Korea window fields according to native capability flags."""
        use_open_level = getattr(vehicle, "window_safety_option2", None) in (3, 4)
        use_open_flag = getattr(vehicle, "window_safety_option", None) == 1
        windows = (
            ("front_left_window_is_open", "Cabin.Window.Row1.Driver"),
            ("front_right_window_is_open", "Cabin.Window.Row1.Passenger"),
            ("back_left_window_is_open", "Cabin.Window.Row2.Left"),
            ("back_right_window_is_open", "Cabin.Window.Row2.Right"),
        )
        if not use_open_level and not use_open_flag:
            for attribute, _path in windows:
                setattr(vehicle, attribute, None)
            return

        for attribute, path in windows:
            if vehicle.window_safety_option2 == 4 and attribute.startswith("back_"):
                setattr(vehicle, attribute, None)
                continue
            if use_open_level:
                raw_value = get_child_value(state, f"{path}.OpenLevel")
                is_open = raw_value in (1, 2, 3) if raw_value is not None else None
            else:
                raw_value = get_child_value(state, f"{path}.Open")
                is_open = raw_value == 1 if raw_value is not None else None
            setattr(vehicle, attribute, is_open)

    def get_vehicle_capabilities(
        self, token: Token, vehicle: Vehicle
    ) -> dict[str, Any]:
        """Return the vehicle feature flags published by MyHyundai Korea."""
        data = self._domestic_post(
            token,
            f"{self.STATUS_PATH}/infolist_v2.do",
            {
                "CCID": f"{self._ensure_cc_id(token).removesuffix('_BLU')}_BLU",
                "autoLoginYn": "Y",
                "carID": vehicle.id,
                "profileIndex": "1",
                "userID": self._ensure_user_id(token),
                "ServiceNo": "C3",
            },
        )
        ccs2_support = data.get("ccuCCS2ProtocolSupport")
        if isinstance(ccs2_support, int):
            vehicle.ccu_ccs2_protocol_support = ccs2_support
        app_mode = data.get("appMode")
        if isinstance(app_mode, str):
            vehicle.remote_control_generation = app_mode
        start_yn = data.get("startYn")
        if isinstance(start_yn, str):
            # The current app treats every value except an explicit N as
            # eligible for remote engine/climate start.
            vehicle.supports_remote_start = start_yn.upper() != "N"
        remote_control_time = data.get("remoteControlTime")
        if isinstance(remote_control_time, int):
            vehicle.remote_control_waiting_time = remote_control_time
        hvac_temp_type = data.get("hvacTempType")
        if isinstance(hvac_temp_type, int):
            vehicle.hvac_temperature_type = hvac_temp_type
        window_safety_option = data.get("windowSafetyOption")
        if isinstance(window_safety_option, int):
            vehicle.window_safety_option = window_safety_option
        window_safety_option2 = data.get("windowSafetyOption2")
        if isinstance(window_safety_option2, int):
            vehicle.window_safety_option2 = window_safety_option2
        vehicle.window_status_capabilities_loaded = True

        seat_info = data.get("seatHeaterVentInfo")
        if isinstance(seat_info, list) and seat_info and isinstance(seat_info[0], dict):
            seats = seat_info[0]
        else:
            seats = data
        for api_field, _option_field, capability_field in self.SEAT_CLIMATE_FIELDS:
            setattr(vehicle, capability_field, seats.get(api_field))

        steering_option = data.get("strgWhlHeatOption")
        stepped_steering_option = data.get("strgWhlHeatingOption")
        vehicle.steering_wheel_heater_option = (
            steering_option if isinstance(steering_option, int) else None
        )
        vehicle.steering_wheel_heating_option = (
            stepped_steering_option
            if isinstance(stepped_steering_option, int)
            else None
        )
        if isinstance(stepped_steering_option, int):
            vehicle.supports_steering_wheel_heater = stepped_steering_option > 0
        elif isinstance(steering_option, int):
            vehicle.supports_steering_wheel_heater = steering_option > 0
        else:
            vehicle.supports_steering_wheel_heater = None
        return data

    def _update_vehicle_properties_ccs2(
        self, vehicle: Vehicle, state: dict[str, Any]
    ) -> None:
        """Apply shared CCS2 status parsing plus the Korea status timestamp."""
        super()._update_vehicle_properties_ccs2(vehicle, state)
        status_date = get_child_value(state, "Date")
        if status_date:
            utc_date = parse_datetime(status_date, dt.UTC)
            if utc_date is not None:
                vehicle.last_updated_at = utc_date.astimezone(vehicle.timezone)

    def force_refresh_vehicle_state(self, token: Token, vehicle: Vehicle) -> None:
        """Wake a vehicle and wait until its cached status becomes fresh."""
        previous_updated_at = vehicle.last_updated_at
        self._domestic_post(
            token,
            f"{self.STATUS_PATH}/carstatus_ccs2.do",
            self._status_body(token, vehicle, "F53"),
            ccs2_support=vehicle.ccu_ccs2_protocol_support,
        )
        # The refresh endpoint only acknowledges the asynchronous request.
        # Korea has no REST polling endpoint for this operation; the app waits
        # for MQTT. The cached endpoint may return HTTP 500 while the vehicle's
        # response is being stored, so retry that read without waking the car
        # again.
        readback_delays = (25, 10, 10)
        for attempt, delay in enumerate(readback_delays):
            time.sleep(delay)
            try:
                self.update_vehicle_with_cached_state(token, vehicle)
            except ServiceTemporaryUnavailable:
                if attempt == len(readback_delays) - 1:
                    raise
            else:
                if (
                    previous_updated_at is None
                    or vehicle.last_updated_at != previous_updated_at
                ):
                    return
                if attempt == len(readback_delays) - 1:
                    raise ServiceTemporaryUnavailable(
                        "Hyundai Korea force refresh completed, but cached vehicle "
                        "status did not update"
                    )

    def _get_control_token(self, token: Token) -> str:
        """Return a short-lived PIN-authorized token for protected controls."""
        now = time.time()
        if token.control_token and token.control_token_expiry > now:
            return token.control_token
        if not token.pin:
            raise PINMissingError("A PIN is required to unlock a Hyundai Korea vehicle")

        headers = self._get_cci_headers(
            token.device_id or "",
            cci_access_token=token.cci_access_token,
            non_ccs_token=token.non_ccs_token,
            exchangeable_token=token.exchangeable_token,
            content_type="application/json",
        )
        response = requests.post(
            self.CCI_DOMAIN_API_URL + "v1/auth/pin",
            headers=headers,
            json={"pin": token.pin},
            timeout=(5, 30),
        )
        if response.status_code == 401:
            raise AuthenticationError("Hyundai Korea PIN authorization failed")
        if response.status_code >= 400:
            raise APIError(
                f"Hyundai Korea PIN authorization failed: HTTP {response.status_code}"
            )
        data = response.json()
        control_info = data.get("controlTokenInfo", {})
        control_token = control_info.get("controlToken")
        if not control_token:
            raise AuthenticationError("Hyundai Korea PIN was rejected")
        token.control_token = str(control_token)
        token.control_token_expiry = now + 240
        return token.control_token

    @classmethod
    def _seat_climate_payload(
        cls, vehicle: Vehicle, options: ClimateRequestOptions
    ) -> list[dict] | None:
        """Build requested seat states and turn omitted supported seats off."""
        seats = {
            api_field: (
                getattr(options, option_field),
                getattr(vehicle, capability_field, None),
            )
            for api_field, option_field, capability_field in cls.SEAT_CLIMATE_FIELDS
        }
        requested = {
            key: value if value is not None else 2
            for key, (value, capability) in seats.items()
            if value is not None or capability not in (None, 0, 7)
        }
        return [requested] if requested else None

    @staticmethod
    def _drop_none(body: dict[str, Any]) -> dict[str, Any]:
        """Remove optional fields that the Korea API does not accept as null."""
        return {key: value for key, value in body.items() if value is not None}

    def _ensure_gen2_control(
        self, token: Token, vehicle: Vehicle, require_start: bool
    ) -> None:
        """Require GEN2 control and, when needed, remote-start eligibility."""
        if getattr(vehicle, "remote_control_generation", None) is None or (
            require_start and getattr(vehicle, "supports_remote_start", None) is None
        ):
            self.get_vehicle_capabilities(token, vehicle)
        if getattr(vehicle, "remote_control_generation", None) != "GEN2":
            mode = getattr(vehicle, "remote_control_generation", None) or "unknown"
            raise APIError(
                f"Hyundai Korea GEN2 remote control is unavailable (appMode={mode})"
            )
        if (
            require_start
            and getattr(vehicle, "supports_remote_start", None) is not True
        ):
            raise APIError(
                "This vehicle is not eligible for remote engine/climate start"
            )

    def _validate_climate_options(
        self, vehicle: Vehicle, options: ClimateRequestOptions
    ) -> None:
        """Reject seat and steering requests unsupported by the vehicle."""
        for _api_field, option_field, capability_field in self.SEAT_CLIMATE_FIELDS:
            requested = getattr(options, option_field)
            capability = getattr(vehicle, capability_field, None)
            allowed = self.SEAT_CLIMATE_STATES.get(capability)
            if requested is not None and allowed is None:
                raise APIError(f"{option_field} capability is unavailable")
            if requested is not None and requested not in allowed:
                raise APIError(
                    f"{option_field} state {requested} is unsupported by capability "
                    f"{capability}"
                )
        if options.steering_wheel not in (None, 0, 1, 2):
            raise APIError("steering_wheel must be 0 (off), 1 (on), or 2 (high)")
        if (
            options.steering_wheel == 2
            and getattr(vehicle, "steering_wheel_heater_option", None) != 2
        ):
            raise APIError(
                "steering_wheel state 2 requires two-level steering-wheel control"
            )
        if options.steering_wheel not in (None, 0):
            steering_supported = getattr(
                vehicle, "supports_steering_wheel_heater", None
            )
            if steering_supported is None:
                raise APIError("steering-wheel capability is unavailable")
            if steering_supported is False:
                raise APIError("This vehicle does not support a heated steering wheel")

    @staticmethod
    def _combined_heating_state(options: ClimateRequestOptions) -> int:
        """Map rear-window and steering choices to the legacy heating code."""
        heating = options.heating if options.heating is not None else 0
        rear_on = heating in (1, 2, 4)
        wheel_on = (
            options.steering_wheel > 0
            if options.steering_wheel is not None
            else heating in (1, 3, 4)
        )
        if rear_on and wheel_on:
            return 4
        if rear_on:
            return 2
        if wheel_on:
            return 3
        return 0

    @staticmethod
    def _control_request_id(data: dict[str, Any]) -> str:
        """Return the request identifier from any Korea control response."""
        request_id = data.get("svcSID") or data.get("SID") or data.get("ServiceNo")
        if not request_id:
            raise APIError("Hyundai Korea control response contained no request ID")
        return str(request_id)

    def _remote_control_post(
        self,
        token: Token,
        vehicle: Vehicle,
        operation: str,
        body: dict[str, Any],
    ) -> str:
        """Send a GEN2 control request and return its asynchronous request ID."""
        if token.pin:
            endpoint = f"api/v2/{self.REMOTE_PATH}/ccsp/pin/{operation}.do"
            authorization = f"Bearer {self._get_control_token(token)}"
        else:
            endpoint = f"api/v1/{self.REMOTE_PATH}/ccsp/{operation}.do"
            authorization = None
        data = self._domestic_post(
            token,
            endpoint,
            body,
            authorization=authorization,
            ccs2_support=vehicle.ccu_ccs2_protocol_support,
        )
        return self._control_request_id(data)

    @classmethod
    def _seat_off_payload(cls, vehicle: Vehicle) -> list[dict] | None:
        """Build off states for every seat with an advertised capability."""
        seats = {
            api_field: getattr(vehicle, capability_field, None)
            for api_field, _option_field, capability_field in cls.SEAT_CLIMATE_FIELDS
        }
        off = {
            key: 2
            for key, capability in seats.items()
            if capability not in (None, 0, 7)
        }
        return [off] if off else None

    def _climate_body(
        self,
        token: Token,
        vehicle: Vehicle,
        command: str,
        options: ClimateRequestOptions | None = None,
    ) -> tuple[str, dict[str, Any]]:
        """Build the Korea engine or EV climate endpoint and request body."""
        body: dict[str, Any] = {
            "CCID": f"{self._ensure_cc_id(token).removesuffix('_BLU')}_BLU",
            "carID": vehicle.id,
            "CMD": command,
        }
        if vehicle.engine_type == ENGINE_TYPES.EV:
            body["ServiceNo"] = self.CLIMATE_SERVICE_NO
            if options is not None:
                duration = options.duration if options.duration is not None else 10
                heating = options.heating if options.heating is not None else 0
                separate_heating = (
                    getattr(vehicle, "steering_wheel_heater_option", None) is not None
                )
                body.update(
                    {
                        "airTemp": f"{options.set_temp if options.set_temp is not None else 21.0:.1f}",
                        "defrost": (
                            options.defrost if options.defrost is not None else False
                        ),
                        "hvacTempType": getattr(vehicle, "hvac_temperature_type", None)
                        or 1,
                        "seatHeaterVentInfo": self._seat_climate_payload(
                            vehicle, options
                        ),
                        "heating1": (
                            None
                            if separate_heating
                            else str(self._combined_heating_state(options))
                        ),
                        "sideRearMirrorHeating": (
                            (1 if heating in (1, 2, 4) else 0)
                            if separate_heating
                            else None
                        ),
                        "wheelHeating": (
                            options.steering_wheel if separate_heating else None
                        ),
                        "ignitionDuration": str(duration),
                    }
                )
            return "remoteclimate_ccs2", self._drop_none(body)

        body["ServiceNo"] = self.ENGINE_SERVICE_NO
        if options is not None:
            duration = options.duration if options.duration is not None else 2
            heating = options.heating if options.heating is not None else 0
            separate_heating = (
                getattr(vehicle, "steering_wheel_heater_option", None) is not None
            )
            body.update(
                {
                    "AirCon": (
                        "1" if options.climate is None or options.climate else "0"
                    ),
                    "defrost": "1" if options.defrost else "2",
                    "Remain": str(duration),
                    "temp": f"{options.set_temp if options.set_temp is not None else 21.0:.1f}",
                    "hvacTempType": getattr(vehicle, "hvac_temperature_type", None)
                    or 1,
                    "HeatingList": (
                        None
                        if separate_heating
                        else [{"heating1": str(self._combined_heating_state(options))}]
                    ),
                    "seatHeaterVentInfo": self._seat_climate_payload(vehicle, options),
                    "sideRearMirrorHeating": (
                        (1 if heating in (1, 2, 4) else 0) if separate_heating else None
                    ),
                    "wheelHeating": (
                        options.steering_wheel if separate_heating else None
                    ),
                }
            )
        else:
            body.update(
                {
                    "Remain": "0",
                    "seatHeaterVentInfo": self._seat_off_payload(vehicle),
                }
            )
        return "engine_ccs2", self._drop_none(body)

    def start_climate(
        self, token: Token, vehicle: Vehicle, options: ClimateRequestOptions
    ) -> str:
        """Start a GEN2 engine/climate session with the requested cabin options."""
        self._ensure_gen2_control(token, vehicle, require_start=True)
        self._validate_climate_options(vehicle, options)
        operation, body = self._climate_body(token, vehicle, "1", options)
        return self._remote_control_post(token, vehicle, operation, body)

    def stop_climate(self, token: Token, vehicle: Vehicle) -> str:
        """Stop a GEN2 engine/climate session."""
        self._ensure_gen2_control(token, vehicle, require_start=False)
        operation, body = self._climate_body(token, vehicle, "2")
        return self._remote_control_post(token, vehicle, operation, body)

    def lock_action(
        self, token: Token, vehicle: Vehicle, action: VEHICLE_LOCK_ACTION
    ) -> str:
        """Lock or unlock a Korea GEN2 vehicle and return the request ID."""
        body = self._status_body(
            token,
            vehicle,
            "F66" if action == VEHICLE_LOCK_ACTION.LOCK else "F67",
        )
        if action == VEHICLE_LOCK_ACTION.LOCK:
            endpoint = f"api/v1/{self.REMOTE_PATH}/ccsp/doorlock.do"
            data = self._domestic_post(
                token,
                endpoint,
                body,
                ccs2_support=vehicle.ccu_ccs2_protocol_support,
            )
        elif action == VEHICLE_LOCK_ACTION.UNLOCK:
            endpoint = f"api/v2/{self.REMOTE_PATH}/ccsp/pin/doorunlock.do"
            control_token = self._get_control_token(token)
            data = self._domestic_post(
                token,
                endpoint,
                body,
                authorization=f"Bearer {control_token}",
                ccs2_support=vehicle.ccu_ccs2_protocol_support,
            )
        else:
            raise APIError(f"Unsupported lock action: {action}")

        return self._control_request_id(data)
