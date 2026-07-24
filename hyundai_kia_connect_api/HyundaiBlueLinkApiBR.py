"""HyundaiBlueLinkApiBR.py"""

# pylint:disable=logging-fstring-interpolation,invalid-name,broad-exception-caught,unused-argument,missing-function-docstring,line-too-long

import datetime as dt
import logging
import typing as ty
from datetime import timedelta
from time import sleep
from urllib.parse import parse_qs, urljoin, urlparse

from .ApiImpl import (
    ApiImplSession,
    ClimateRequestOptions,
    WindowRequestOptions,
)
from .ApiImplType1 import ApiImplType1
from .const import (
    BRAND_HYUNDAI,
    BRANDS,
    DOMAIN,
    ENGINE_TYPES,
    ORDER_STATUS,
    VEHICLE_LOCK_ACTION,
    WINDOW_STATE,
)
from .exceptions import APIError, AuthenticationError
from .Token import Token
from .utils import get_index_into_hex_temp
from .Vehicle import DayTripCounts, DayTripInfo, MonthTripInfo, TripInfo, Vehicle

_LOGGER = logging.getLogger(__name__)

# The Brazilian signin endpoint returns {"step": N} (HTTP 200, no redirectUrl)
# when the account must complete an action in the Bluelink app / web portal
# before OAuth can proceed. The step numbers map to the routes handled by
# toStep() in the login SPA bundle (/web/v1/user static JS).
_SIGNIN_STEP_MESSAGES = {
    0: "the account must accept the terms of service",
    3: "the account must accept the data-access agreement",
    4: "the account must re-accept updated terms of service",
    5: "the account password has expired and must be reset",
    6: "the account is not activated yet",
    7: "identity verification is required",
    8: "identity verification is required",
    9: "the account is blocked",
    10: "email verification is required",
    11: "the account email must be changed",
    12: "identity verification is required",
    13: "email verification is required",
}


class HyundaiBlueLinkApiBR(ApiImplType1):
    """Brazilian Hyundai BlueLink API implementation.

    Extends ApiImplType1 to reuse its CCS2 status parser
    (``_update_vehicle_properties_ccs2``). BR overrides all of its own auth,
    headers, endpoint selection and force-refresh flow below; only the CCS2
    parsing is inherited.
    """

    supports_window_control: bool = True
    # BR does not implement valet mode; keep it off (ApiImplType1 defaults True).
    supports_valet_mode: bool = False
    data_timezone = dt.timezone(dt.timedelta(hours=-3))  # Brazil (BRT/BRST)

    # Async force-refresh polling: how long to wait for the vehicle to report
    # a fresh snapshot to /ccs2/carstatus/latest after triggering /ccs2/carstatus.
    _FORCE_REFRESH_POLL_INTERVAL = 5  # seconds between polls
    _FORCE_REFRESH_MAX_POLLS = 12  # ~60s total

    def __init__(self, region: int, brand: int, language: str = "pt-BR"):
        if BRANDS[brand] != BRAND_HYUNDAI:
            raise APIError(
                f"Unknown brand {BRANDS[brand]} for region Brazil. "
                "Only Hyundai is supported."
            )

        self.language = language
        self.base_url = "br-ccapi.hyundai.com.br"
        self.api_url = f"https://{self.base_url}/api/v1/"
        self.api_v2_url = f"https://{self.base_url}/api/v2/"
        self.ccsp_device_id = "c6e5815b-3057-4e5e-95d5-e3d5d1d2093e"
        self.ccsp_service_id = "03f7df9b-7626-4853-b7bd-ad1e8d722bd5"
        self.ccsp_application_id = "513a491a-0d7c-4d6a-ac03-a2df127d73b0"
        self.basic_authorization_header = (
            "Basic MDNmN2RmOWItNzYyNi00ODUzLWI3YmQtYWQxZThkNzIyYmQ1On"
            "lRejJiYzZDbjhPb3ZWT1I3UkRXd3hUcVZ3V0czeUtCWUZEZzBIc09Yc3l4eVBsSA=="
        )

        self.api_headers = {
            "Content-Type": "application/json; charset=UTF-8",
            "Accept": "application/json, text/plain, */*",
            "Accept-Encoding": "br;q=1.0, gzip;q=0.9, deflate;q=0.8",
            "Accept-Language": "pt-BR;q=1.0, en-US;q=0.9",
            "User-Agent": "BR_BlueLink/1.0.14 (com.hyundai.bluelink.br; build:10132; iOS 18.4.0) Alamofire/5.9.1",
            "Host": self.base_url,
            "offset": "-3",
            "ccuCCS2ProtocolSupport": "0",
        }

        self.session = ApiImplSession()
        self.temperature_range = range(62, 82)

    def _build_api_url(self, path: str) -> str:
        """Build full API URL from path."""
        return urljoin(self.api_url, path.lstrip("/"))

    def _build_api_v2_url(self, path: str) -> str:
        """Build API v2 URL from path."""
        return urljoin(self.api_v2_url, path.lstrip("/"))

    def _get_authenticated_headers(self, token: Token) -> dict:
        """Get headers with authentication."""
        headers = dict(self.api_headers)
        device_id = token.device_id or self.ccsp_device_id
        headers["ccsp-device-id"] = device_id
        headers["ccsp-application-id"] = self.ccsp_application_id
        headers["Authorization"] = f"Bearer {token.access_token}"
        return headers

    def _get_cookies(self) -> dict:
        """Request cookies from the API for authentication."""
        params = {
            "response_type": "code",
            "client_id": self.ccsp_service_id,
            "redirect_uri": self._build_api_url("/user/oauth2/redirect"),
        }

        url = self._build_api_url("/user/oauth2/authorize")
        _LOGGER.debug(f"{DOMAIN} - Requesting cookies from {url}")
        response = self.session.get(url, params=params)
        response.raise_for_status()
        # The session cookie ('account') is set during the 302 redirect, so it
        # lives in the session cookie jar rather than on the final response.
        return self.session.cookies.get_dict()

    def _get_authorization_code(
        self, cookies: dict, username: str, password: str
    ) -> str:
        """Get authorization code from redirect URL."""
        url = self._build_api_url("/user/signin")
        data = {"email": username, "password": password}

        headers = {
            "Referer": "https://br-ccapi.hyundai.com.br/web/v1/user/signin",
            "Accept-Encoding": "gzip, deflate, br",
            "Accept": "*/*",
            "Connection": "keep-alive",
            "Content-Type": "text/plain;charset=UTF-8",
            "Host": self.api_headers["Host"],
            "Accept-Language": "pt-BR,en-US;q=0.9,en;q=0.8",
            "Origin": "https://br-ccapi.hyundai.com.br",
            "User-Agent": (
                "Mozilla/5.0 (iPhone; CPU iPhone OS 18_4 like Mac OS X) "
                "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148_CCS_APP_iOS"
            ),
        }

        response = self.session.post(url, json=data, cookies=cookies, headers=headers)
        response.raise_for_status()
        response_data = response.json()

        redirect_url = response_data.get("redirectUrl")
        if not redirect_url:
            # The account authenticated but must complete an action before
            # OAuth can proceed (see _SIGNIN_STEP_MESSAGES). Surface a clear
            # message instead of a raw KeyError on "redirectUrl".
            step = response_data.get("step")
            reason = _SIGNIN_STEP_MESSAGES.get(step)
            if reason is not None:
                raise AuthenticationError(
                    f"Brazilian Hyundai login incomplete: {reason} "
                    f"(signin step={step}). Complete this in the Bluelink app "
                    "or web portal, then retry."
                )
            raise AuthenticationError(
                "Brazilian Hyundai login failed: no redirectUrl in signin "
                f"response (keys={sorted(response_data.keys())}). "
                "Check your username and password."
            )

        _LOGGER.debug(f"{DOMAIN} - Got redirect URL")
        parsed_url = urlparse(redirect_url)
        code_list = parse_qs(parsed_url.query).get("code")
        if not code_list:
            raise AuthenticationError(
                "Brazilian Hyundai login failed: no authorization code in redirect URL."
            )
        return code_list[0]

    def _get_auth_response(self, authorization_code: str) -> dict:
        """Request access token from the API."""
        url = self._build_api_url("/user/oauth2/token")
        body = {
            "client_id": self.ccsp_service_id,
            "grant_type": "authorization_code",
            "code": authorization_code,
            "redirect_uri": self._build_api_url("/user/oauth2/redirect"),
        }
        headers = {
            "Content-Type": "application/x-www-form-urlencoded; charset=utf-8",
            "User-Agent": self.api_headers["User-Agent"],
            "Authorization": self.basic_authorization_header,
        }

        response = self.session.post(url, data=body, headers=headers)
        response.raise_for_status()
        return response.json()

    def login(
        self,
        username: str,
        password: str,
        otp_handler: ty.Callable[[dict], dict] | None = None,
        pin: str | None = None,
    ) -> Token:
        """Login to Brazilian Hyundai API."""
        _LOGGER.debug(f"{DOMAIN} - Logging in to Brazilian API")

        cookies = self._get_cookies()
        authorization_code = self._get_authorization_code(cookies, username, password)
        auth_response = self._get_auth_response(authorization_code)

        expires_in_seconds = auth_response["expires_in"]
        expires_at = dt.datetime.now(dt.timezone.utc) + timedelta(
            seconds=expires_in_seconds
        )

        return Token(
            access_token=auth_response["access_token"],
            refresh_token=auth_response["refresh_token"],
            valid_until=expires_at,
            username=username,
            password=password,
            device_id=self.ccsp_device_id,
            pin=pin,
        )

    def get_vehicles(self, token: Token) -> list:
        """Get list of vehicles."""
        url = self._build_api_url("/spa/vehicles")
        headers = self._get_authenticated_headers(token)

        response = self.session.get(url, headers=headers)
        response.raise_for_status()
        response_data = response.json()
        _LOGGER.debug(f"{DOMAIN} - Got vehicles response: {response_data}")
        if "resMsg" not in response_data or "vehicles" not in response_data.get(
            "resMsg", {}
        ):
            raise APIError("Missing resMsg or vehicles in response")
        result = []
        for entry in response_data["resMsg"]["vehicles"]:
            # Map vehicle type to engine type
            vehicle_type = entry["type"]
            if vehicle_type == "GN":
                entry_engine_type = ENGINE_TYPES.ICE
            elif vehicle_type == "EV":
                entry_engine_type = ENGINE_TYPES.EV
            elif vehicle_type in ["PHEV", "PE"]:
                entry_engine_type = ENGINE_TYPES.PHEV
            elif vehicle_type == "HV":
                entry_engine_type = ENGINE_TYPES.HEV
            else:
                entry_engine_type = ENGINE_TYPES.ICE

            vehicle = Vehicle(
                id=entry["vehicleId"],
                name=entry["nickname"],
                model=entry["vehicleName"],
                registration_date=entry["regDate"],
                VIN=entry["vin"],
                timezone=self.data_timezone,
                engine_type=entry_engine_type,
                ccu_ccs2_protocol_support=entry.get("ccuCCS2ProtocolSupport", 0),
            )
            result.append(vehicle)

        return result

    def _get_cached_vehicle_state(self, token: Token, vehicle: Vehicle) -> dict:
        """Return the server-cached vehicle status (does not wake the car).

        Despite BR vehicles reporting ccuCCS2ProtocolSupport=0, the cached
        status is served in CCS2 format at /ccs2/carstatus/latest, including
        location. The legacy /status/latest endpoint returns HTTP 503
        (resCode 5031, "Unavailable remote control") on BR — it is the
        remote-control result endpoint, not a status read.
        """
        url = self._build_api_url(f"/spa/vehicles/{vehicle.id}/ccs2/carstatus/latest")
        headers = self._get_authenticated_headers(token)
        response = self.session.get(url, headers=headers)
        response.raise_for_status()
        return response.json()["resMsg"]["state"]["Vehicle"]

    def update_vehicle_with_cached_state(self, token: Token, vehicle: Vehicle) -> None:
        """Update with the server-cached CCS2 state (does not wake the car)."""
        state = self._get_cached_vehicle_state(token, vehicle)
        self._update_vehicle_properties_ccs2(vehicle, state)

    def force_refresh_vehicle_state(self, token: Token, vehicle: Vehicle) -> None:
        """Force a fresh reading from the vehicle (wakes the car).

        The BR CCS2 force is asynchronous: GET /ccs2/carstatus only returns an
        acknowledgement, then the vehicle pushes a fresh snapshot to
        /ccs2/carstatus/latest a few seconds later (its lastUpdateTime
        advances). Trigger the refresh, poll until the timestamp changes, then
        parse the fresh state; fall back to the last cached snapshot if the
        vehicle does not report in time.
        """
        latest_url = self._build_api_url(
            f"/spa/vehicles/{vehicle.id}/ccs2/carstatus/latest"
        )
        previous = self.session.get(
            latest_url, headers=self._get_authenticated_headers(token)
        )
        previous.raise_for_status()
        previous_update = previous.json()["resMsg"].get("lastUpdateTime")

        trigger_url = self._build_api_url(f"/spa/vehicles/{vehicle.id}/ccs2/carstatus")
        _LOGGER.debug(f"{DOMAIN} - Triggering async force refresh")
        trigger = self.session.get(
            trigger_url, headers=self._get_authenticated_headers(token)
        )
        trigger.raise_for_status()

        state = None
        for _ in range(self._FORCE_REFRESH_MAX_POLLS):
            sleep(self._FORCE_REFRESH_POLL_INTERVAL)
            response = self.session.get(
                latest_url, headers=self._get_authenticated_headers(token)
            )
            response.raise_for_status()
            resmsg = response.json()["resMsg"]
            if resmsg.get("lastUpdateTime") != previous_update:
                state = resmsg["state"]["Vehicle"]
                break

        if state is None:
            _LOGGER.warning(
                f"{DOMAIN} - Force refresh did not report new data in time; "
                "using the latest cached snapshot"
            )
            state = self._get_cached_vehicle_state(token, vehicle)

        self._update_vehicle_properties_ccs2(vehicle, state)

    def _ensure_control_token(self, token: Token) -> str:
        """Ensure we have a valid control token for remote commands."""
        control_token = getattr(token, "control_token", None)
        expires_at = getattr(token, "control_token_expires_at", None)
        if (
            control_token
            and expires_at
            and expires_at - dt.timedelta(seconds=5) > dt.datetime.now(dt.timezone.utc)
        ):
            return control_token

        if not token.pin:
            raise APIError("PIN is required for remote commands.")

        device_id = token.device_id or self.ccsp_device_id
        token.device_id = device_id

        url = self._build_api_url("/user/pin")
        headers = self._get_authenticated_headers(token)
        payload = {"pin": token.pin, "deviceId": device_id}

        response = self.session.put(url, json=payload, headers=headers)
        response.raise_for_status()
        data = response.json()

        if data.get("controlToken") is None:
            raise APIError("Failed to obtain control token.")

        control_token = f"Bearer {data['controlToken']}"
        expires_in = data.get("expiresTime", 0)
        expires_at = dt.datetime.now(dt.timezone.utc) + dt.timedelta(
            seconds=expires_in or 600
        )

        token.control_token = control_token
        token.control_token_expires_at = expires_at
        return control_token

    def lock_action(
        self, token: Token, vehicle: Vehicle, action: VEHICLE_LOCK_ACTION
    ) -> str:
        """Lock or unlock the vehicle."""
        control_token = self._ensure_control_token(token)
        device_id = token.device_id or self.ccsp_device_id

        url = self._build_api_v2_url(
            f"spa/vehicles/{vehicle.id}/control/door",
        )
        headers = self._get_authenticated_headers(token)
        headers["Authorization"] = control_token
        headers["ccsp-device-id"] = device_id
        headers["ccuCCS2ProtocolSupport"] = str(vehicle.ccu_ccs2_protocol_support or 0)

        payload = {"deviceId": device_id, "action": action.value}
        _LOGGER.debug(f"{DOMAIN} - Lock action request: %s", payload)

        response = self.session.post(url, json=payload, headers=headers)
        response.raise_for_status()
        data = response.json()
        _LOGGER.debug(f"{DOMAIN} - Lock action response: %s", data)

        if data.get("retCode") != "S":
            raise APIError(
                f"Lock action failed: {data.get('resCode')} {data.get('resMsg')}"
            )

        return data.get("msgId")

    def check_action_status(
        self,
        token: Token,
        vehicle: Vehicle,
        action_id: str,
        synchronous: bool = False,
        timeout: int = 0,
    ) -> ORDER_STATUS:
        """Check status of a previously submitted remote command."""
        if synchronous:
            if timeout < 1:
                raise APIError("Timeout must be 1 or higher for synchronous checks.")

            end_time = dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=timeout)
            while dt.datetime.now(dt.timezone.utc) < end_time:
                state = self.check_action_status(
                    token, vehicle, action_id, synchronous=False
                )
                if state == ORDER_STATUS.PENDING:
                    sleep(5)
                    continue
                return state

            return ORDER_STATUS.TIMEOUT

        url = self._build_api_url(f"/spa/notifications/{vehicle.id}/records")
        headers = self._get_authenticated_headers(token)

        response = self.session.get(url, headers=headers)
        response.raise_for_status()
        data = response.json()
        _LOGGER.debug(f"{DOMAIN} - Action status response: %s", data)

        records = data.get("resMsg", [])
        for record in records:
            if record.get("recordId") != action_id:
                continue

            result = (record.get("result") or "").lower()
            if result == "success":
                return ORDER_STATUS.SUCCESS
            if result == "fail":
                return ORDER_STATUS.FAILED
            if result == "non-response":
                return ORDER_STATUS.TIMEOUT
            if result in ("", "pending", None):
                return ORDER_STATUS.PENDING

        return ORDER_STATUS.UNKNOWN

    def set_windows_state(
        self, token: Token, vehicle: Vehicle, options: WindowRequestOptions
    ) -> str:
        """Open or close all windows (BR API controls all windows together)."""
        control_token = self._ensure_control_token(token)
        device_id = token.device_id or self.ccsp_device_id

        url = self._build_api_v2_url(f"spa/vehicles/{vehicle.id}/control/window")

        # Brazilian API uses simple action for all windows at once
        # Check if any window should be open, otherwise close
        action = "open"
        if options.front_left == WINDOW_STATE.CLOSED:
            action = "close"
        elif options.front_right == WINDOW_STATE.CLOSED:
            action = "close"
        elif options.back_left == WINDOW_STATE.CLOSED:
            action = "close"
        elif options.back_right == WINDOW_STATE.CLOSED:
            action = "close"

        headers = self._get_authenticated_headers(token)
        headers["Authorization"] = control_token
        headers["ccsp-device-id"] = device_id
        headers["ccuCCS2ProtocolSupport"] = str(vehicle.ccu_ccs2_protocol_support or 0)

        payload = {"action": action, "deviceId": device_id}
        _LOGGER.debug(f"{DOMAIN} - Window action request: {payload}")

        response = self.session.post(url, json=payload, headers=headers)
        response.raise_for_status()
        data = response.json()
        _LOGGER.debug(f"{DOMAIN} - Window action response: {data}")

        if data.get("retCode") != "S":
            raise APIError(
                f"Window action failed: {data.get('resCode')} {data.get('resMsg')}"
            )

        return data.get("msgId")

    def start_hazard_lights(self, token: Token, vehicle: Vehicle) -> str:
        """Turn on hazard lights (lights only, no horn)."""
        control_token = self._ensure_control_token(token)
        device_id = token.device_id or self.ccsp_device_id

        url = self._build_api_v2_url(f"spa/vehicles/{vehicle.id}/control/light")
        headers = self._get_authenticated_headers(token)
        headers["Authorization"] = control_token
        headers["ccsp-device-id"] = device_id
        headers["ccuCCS2ProtocolSupport"] = str(vehicle.ccu_ccs2_protocol_support or 0)

        _LOGGER.debug(f"{DOMAIN} - Hazard lights request")

        response = self.session.post(url, headers=headers)
        response.raise_for_status()
        data = response.json()
        _LOGGER.debug(f"{DOMAIN} - Hazard lights response: {data}")

        if data.get("retCode") != "S":
            raise APIError(
                f"Hazard lights failed: {data.get('resCode')} {data.get('resMsg')}"
            )

        return data.get("msgId")

    def get_notification_history(self, token: Token, vehicle: Vehicle) -> list:
        """Get notification history (for debugging and tracking command results)."""
        url = self._build_api_url(f"/spa/notifications/{vehicle.id}/history")
        headers = self._get_authenticated_headers(token)

        response = self.session.get(url, headers=headers)
        response.raise_for_status()
        data = response.json()
        _LOGGER.debug(f"{DOMAIN} - Notification history response")

        return data.get("resMsg", [])

    def start_climate(
        self, token: Token, vehicle: Vehicle, options: ClimateRequestOptions
    ) -> str:
        """Start climate control with temperature and seat heating settings."""
        control_token = self._ensure_control_token(token)
        device_id = token.device_id or self.ccsp_device_id

        url = self._build_api_v2_url(f"spa/vehicles/{vehicle.id}/control/engine")

        # Set defaults
        if options.set_temp is None:
            options.set_temp = 21  # 21°C default
        if options.duration is None:
            options.duration = 10  # 10 minutes default
        if options.defrost is None:
            options.defrost = False
        if options.climate is None:
            options.climate = True
        if options.heating is None:
            options.heating = 0
        if options.front_left_seat is None:
            options.front_left_seat = 0

        # Convert temperature to hex code
        # BR API uses direct Celsius value converted to hex
        temp_celsius = int(options.set_temp)
        temp_code = get_index_into_hex_temp(temp_celsius)

        # Map seat heating level (0-5 in ClimateRequestOptions to 0-8 for BR API)
        # 0=off, 1-3=heat levels, 4-5=cool levels (BR uses similar mapping)
        seat_heat_cmd = options.front_left_seat if options.front_left_seat else 0

        headers = self._get_authenticated_headers(token)
        headers["Authorization"] = control_token
        headers["ccsp-device-id"] = device_id
        headers["ccuCCS2ProtocolSupport"] = str(vehicle.ccu_ccs2_protocol_support or 0)

        payload = {
            "action": "start",
            "options": {
                "airCtrl": 1 if options.climate else 0,
                "heating1": int(options.heating),
                "seatHeaterVentCMD": {"drvSeatOptCmd": seat_heat_cmd},
                "defrost": options.defrost,
                "igniOnDuration": options.duration,
            },
            "hvacType": 1,
            "deviceId": device_id,
            "tempCode": temp_code,
            "unit": "C",
        }

        _LOGGER.debug(f"{DOMAIN} - Start climate request: {payload}")

        response = self.session.post(url, json=payload, headers=headers)
        response.raise_for_status()
        data = response.json()
        _LOGGER.debug(f"{DOMAIN} - Start climate response: {data}")

        if data.get("retCode") != "S":
            raise APIError(
                f"Start climate failed: {data.get('resCode')} {data.get('resMsg')}"
            )

        return data.get("msgId")

    def stop_climate(self, token: Token, vehicle: Vehicle) -> str:
        """Stop climate control."""
        control_token = self._ensure_control_token(token)
        device_id = token.device_id or self.ccsp_device_id

        url = self._build_api_v2_url(f"spa/vehicles/{vehicle.id}/control/engine")

        headers = self._get_authenticated_headers(token)
        headers["Authorization"] = control_token
        headers["ccsp-device-id"] = device_id
        headers["ccuCCS2ProtocolSupport"] = str(vehicle.ccu_ccs2_protocol_support or 0)

        payload = {"action": "stop", "deviceId": device_id}

        _LOGGER.debug(f"{DOMAIN} - Stop climate request: {payload}")

        response = self.session.post(url, json=payload, headers=headers)
        response.raise_for_status()
        data = response.json()
        _LOGGER.debug(f"{DOMAIN} - Stop climate response: {data}")

        if data.get("retCode") != "S":
            raise APIError(
                f"Stop climate failed: {data.get('resCode')} {data.get('resMsg')}"
            )

        return data.get("msgId")

    def update_month_trip_info(
        self, token: Token, vehicle: Vehicle, yyyymm_string: str
    ) -> None:
        """Update monthly trip info."""
        url = self._build_api_url(f"/spa/vehicles/{vehicle.id}/tripinfo")
        data = {"tripPeriodType": 0, "setTripMonth": yyyymm_string}

        headers = self._get_authenticated_headers(token)
        response = self.session.post(url, json=data, headers=headers)

        try:
            response.raise_for_status()
            trip_data = response.json()["resMsg"]

            if trip_data.get("monthTripDayCnt", 0) > 0:
                result = MonthTripInfo(
                    yyyymm=yyyymm_string,
                    day_list=[],
                    summary=TripInfo(
                        drive_time=trip_data.get("tripDrvTime"),
                        idle_time=trip_data.get("tripIdleTime"),
                        distance=trip_data.get("tripDist"),
                        avg_speed=trip_data.get("tripAvgSpeed"),
                        max_speed=trip_data.get("tripMaxSpeed"),
                    ),
                )

                for day in trip_data.get("tripDayList", []):
                    processed_day = DayTripCounts(
                        yyyymmdd=day["tripDayInMonth"],
                        trip_count=day["tripCntDay"],
                    )
                    result.day_list.append(processed_day)

                vehicle.month_trip_info = result
        except Exception as e:
            _LOGGER.warning(f"{DOMAIN} - Failed to get month trip info: {e}")

    def update_day_trip_info(
        self, token: Token, vehicle: Vehicle, yyyymmdd_string: str
    ) -> None:
        """Update daily trip info."""
        url = self._build_api_url(f"/spa/vehicles/{vehicle.id}/tripinfo")
        data = {"tripPeriodType": 1, "setTripDay": yyyymmdd_string}

        headers = self._get_authenticated_headers(token)
        response = self.session.post(url, json=data, headers=headers)

        try:
            response.raise_for_status()
            trip_data = response.json()["resMsg"]
            day_trip_list = trip_data.get("dayTripList", [])

            if len(day_trip_list) > 0:
                msg = day_trip_list[0]
                result = DayTripInfo(
                    yyyymmdd=yyyymmdd_string,
                    trip_list=[],
                    summary=TripInfo(
                        drive_time=msg.get("tripDrvTime"),
                        idle_time=msg.get("tripIdleTime"),
                        distance=msg.get("tripDist"),
                        avg_speed=msg.get("tripAvgSpeed"),
                        max_speed=msg.get("tripMaxSpeed"),
                    ),
                )

                for trip in msg.get("tripList", []):
                    processed_trip = TripInfo(
                        hhmmss=trip.get("tripTime"),
                        drive_time=trip.get("tripDrvTime"),
                        idle_time=trip.get("tripIdleTime"),
                        distance=trip.get("tripDist"),
                        avg_speed=trip.get("tripAvgSpeed"),
                        max_speed=trip.get("tripMaxSpeed"),
                    )
                    result.trip_list.append(processed_trip)

                vehicle.day_trip_info = result
        except Exception as e:
            _LOGGER.warning(f"{DOMAIN} - Failed to get day trip info: {e}")
