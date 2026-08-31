"""KiaUvoApiCN -- China Bluelink / UVO API implementation (2026 rewrite).

Reverse-engineered from the China Bluelink iOS App 5.05 (build 103, live app
build 109) and verified end-to-end against ``prd.cn-ccapi.hyundai.com`` with a
real account (see case notes: work/bluelink-cn-api/notes/).

Major differences vs the previous KiaUvoApiCN.py implementation (which used a
stale API surface and never worked against the current servers):

1. LOGIN FLOW (completely replaced)
   old:   /api/v1/user/oauth2/authorize  ->  /api/v1/user/signin
          ->  /api/v1/user/oauth2/token (Basic auth code exchange)
   new:   /api/v1/user/oauth2/authorize  (redirect_uri points at the UARS
          callback ``uars-{k|h}.hmgmobility.com.cn/join/ccsp/loginCallback.do``
          and carries a base64url ``state`` blob)
          -> /api/v1/user/signin  (body now also carries ``mobileNum``)
          -> GET the returned redirectUrl: the UARS server exchanges the code
          SERVER-SIDE and returns an HTML page with the full token bundle
          embedded as a JS template literal (``var x = `{...}```).  The old
          ``/api/v1/user/oauth2/token`` endpoint is dead for this flow
          (errCode 4002) and is no longer used.

2. DEVICE REGISTRATION
   ``pushType`` changed from ``GCM`` to ``APNS`` and ``providerDeviceId`` is
   now mandatory in the body.  Response shape (resMsg.deviceId) unchanged.

3. TOKENS
   - access token: JWT RS256, ``Bearer`` prefix, **expiresIn 21600 s (6 h)**
   - uarsToken: JWT HS256, ~1 year, kept for the UARS-side (PIN reset page,
     WeChat binding) --stored per-username on the API instance, not on Token
     (Token dataclass is shared across regions).
   - refresh: ``POST /api/v1/user/silentsignin`` reuses the ccapi session
     cookie to mint a fresh UARS callback code (no password).  Falls back to
     the full password login when the session cookie has expired.

4. HEADERS
   Business requests need ``ccsp-device-id`` (else resCode 4002 "deviceId is
   not exist").  ``Stamp`` is NOT used by China.  APPKEY / ProviderDeviceID /
   UD-UniqueDeviceIdentifier headers exist in the app but are optional
   (verified: business calls succeed without them), so they are not sent.

5. BUSINESS API (vehicles / status / control paths) --unchanged from the old
   implementation and re-verified live: /api/v1/spa/vehicles,
   .../status/latest, .../location etc. return the same schema, so the old
   property-mapping helpers are reused as-is.

Everything marked ``# CN-UNVERIFIED`` below rests on static analysis only and
still needs a live regression pass (see case notes U1-U8).
"""

# pylint:disable=missing-class-docstring,missing-function-docstring,wildcard-import,unused-wildcard-import,invalid-name,logging-fstring-interpolation,broad-except,bare-except,unused-argument,line-too-long,too-many-lines

import base64
import datetime as dt
import json
import logging
import math
import re
import typing as ty
import uuid
from time import sleep
from zoneinfo import ZoneInfo

from .ApiImpl import ClimateRequestOptions
from .ApiImplType1 import ApiImplType1, _check_response_for_errors
from .const import (
    BRAND_HYUNDAI,
    BRAND_KIA,
    BRANDS,
    CHARGE_PORT_ACTION,
    DISTANCE_UNITS,
    DOMAIN,
    ENGINE_TYPES,
    ORDER_STATUS,
    SEAT_STATUS,
    TEMPERATURE_UNITS,
    VEHICLE_LOCK_ACTION,
)
from .exceptions import (
    APIError,
    AuthenticationError,
    UnsupportedControlError,
)
from .Token import Token
from .utils import (
    get_child_value,
    get_hex_temp_into_index,
    get_index_into_hex_temp,
    normalize_battery_soc,
    parse_datetime,
)
from .Vehicle import (
    DailyDrivingStats,
    DayTripCounts,
    DayTripInfo,
    MonthTripInfo,
    TripInfo,
    Vehicle,
)

_LOGGER = logging.getLogger(__name__)

# Live-app User-Agent format (build 109, 2026-08).  The China app is native
# iOS/NSURLSession -- the old okhttp/3.12.0 UA never belonged to this region.
USER_AGENT_BLUELINK_CN: str = "BlueLink/109 CFNetwork/3896.100.1.2.1 Darwin/27.0.0"

# Error handling: reuse ApiImplType1._check_response_for_errors.  Its mapping
# treats resCode 4002 as DeviceIDError, which triggers the inherited
# _retry_on_device_id_error decorator to re-register the device and retry
# once -- exactly the recovery the CN servers need ("deviceId is not exist").
# Rate limiting (resCode 5091 -> RateLimitingError) is covered by the same
# inherited mapping.  Note: the CN gateway also sends X-Ratelimit-* headers,
# but live observation shows they are always 0 even on success, so they carry
# no actionable signal and are ignored here.


def _extract_uars_login_bundle(html: str) -> dict:
    """Extract the UARS login token bundle embedded in the callback HTML page.

    The ``loginCallback.do`` response embeds a JSON document inside a JS
    template literal::

        var xxx = `{"code":0,"status":true,"id":"UARS-COM-040","data":{
            "uarsToken": "...", "tokenCode": "...",
            "ccspToken": {"accessToken": "...", "refreshToken": "...",
                          "tokenType": "Bearer", "expiresIn": 21600},
            "profile": {...}}}`

    Returns the parsed top-level JSON (the ``data`` member carries the tokens),
    or raises AuthenticationError when no bundle is present (e.g. the callback
    rejected the code).
    """
    # Preferred: the template-literal form (live-verified).
    for match in re.finditer(r"=\s*`(\{.*?\})\s*`", html, re.DOTALL):
        try:
            candidate = json.loads(match.group(1))
        except ValueError:
            continue
        if candidate.get("data", {}).get("uarsToken"):
            return candidate
    # Fallback: brace-matched scan for any JSON containing a uarsToken.
    # Walk outward from the nearest "{" so both the bare data object and the
    # full wrapper are recognised (normalised to {"data": ...} on return).
    for match in re.finditer(r'"uarsToken"', html):
        key_at = match.start()
        open_positions = [i for i, ch in enumerate(html[: key_at + 1]) if ch == "{"]
        # nearest brace first, then progressively earlier ones (outer objects)
        for start in reversed(open_positions):
            depth = 0
            for idx in range(start, len(html)):
                char = html[idx]
                if char == "{":
                    depth += 1
                elif char == "}":
                    depth -= 1
                    if depth == 0:
                        try:
                            candidate = json.loads(html[start : idx + 1])
                        except ValueError:
                            break
                        if candidate.get("data", {}).get("uarsToken"):
                            return candidate
                        if candidate.get("uarsToken"):
                            # bare data object (matched from its own "{")
                            return {"data": candidate}
                        break
    raise AuthenticationError(
        "UARS login callback did not return a token bundle. "
        "The session may have expired --please retry the login."
    )


class KiaUvoApiCN(ApiImplType1):
    data_timezone = ZoneInfo("Asia/Shanghai")
    temperature_range = tuple(x * 0.5 for x in range(28, 60))

    def __init__(self, region: int, brand: int, language: str) -> None:
        super().__init__()
        self.LANGUAGE: str = language or "zh"
        # Accept both the numeric BRANDS key (as VehicleManager passes it) and
        # the literal brand string, to be forgiving about caller conventions.
        brand_name = BRANDS.get(brand, brand)
        # CN-UNVERIFIED: Kia constants come from the same binary (NetworkDefines)
        # but only the Hyundai side has been live-verified.
        if brand_name == BRAND_KIA:
            self.BASE_DOMAIN: str = "prd.cn-ccapi.kia.com"
            self.UARS_DOMAIN: str = "uars-k.hmgmobility.com.cn"
            self.CCSP_SERVICE_ID: str = "9d5df92a-06ae-435f-b459-8304f2efcc67"
            self.APP_ID: str = "5519a969-295f-4c5a-a27e-9d9fab2bd50c"
        elif brand_name == BRAND_HYUNDAI:
            self.BASE_DOMAIN: str = "prd.cn-ccapi.hyundai.com"
            self.UARS_DOMAIN: str = "uars-h.hmgmobility.com.cn"
            self.CCSP_SERVICE_ID: str = "72b3d019-5bc7-443d-a437-08f307cf06e2"
            self.APP_ID: str = "b09e4d17-c30c-40f1-a1ec-8ac11d6665cf"
        else:
            raise ValueError(f"Unsupported brand for the China region: {brand!r}")
        # DIFFERENCE vs old implementation: the old APP_IDs (eea8762c---for Kia,
        # ed01581a---for Hyundai) no longer exist in the current app and both
        # were replaced by the values above (live-verified for Hyundai).

        self.BASE_URL: str = self.BASE_DOMAIN
        self.USER_API_URL: str = "https://" + self.BASE_URL + "/api/v1/user/"
        self.SPA_API_URL: str = "https://" + self.BASE_URL + "/api/v1/spa/"
        self.SPA_API_URL_V2: str = "https://" + self.BASE_URL + "/api/v2/spa/"
        self.LOGIN_API_URL: str = "https://" + self.BASE_URL + "/web/v1/user/"
        self.UARS_BASE_URL: str = "https://" + self.UARS_DOMAIN
        self.CLIENT_ID: str = self.CCSP_SERVICE_ID

        # Per-username UARS state (uarsToken/tokenCode/profile).  Kept off the
        # Token dataclass because Token is shared across all regions.
        self._uars_state: dict[str, dict] = {}

    # ------------------------------------------------------------------
    # Headers
    # ------------------------------------------------------------------
    def _get_stamp(self) -> str:
        """China does not use the Stamp header (EU-only).  Present so the
        inherited ``_retry_on_device_id_error`` wrapper keeps working."""
        return ""

    def _get_authenticated_headers(
        self, token: Token, ccs2_support: int | None = None
    ) -> dict:
        # DIFFERENCE vs old implementation: no Stamp header, CN app UA, and the
        # device id header is what the servers actually require.
        headers = {
            "Authorization": token.access_token,
            "ccsp-service-id": self.CCSP_SERVICE_ID,
            "ccsp-application-id": self.APP_ID,
            "ccsp-device-id": token.device_id,
            "Host": self.BASE_URL,
            "Connection": "Keep-Alive",
            "Accept-Encoding": "gzip",
            "User-Agent": USER_AGENT_BLUELINK_CN,
        }
        if ccs2_support is not None:
            headers["Ccuccs2protocolsupport"] = str(ccs2_support)
        return headers

    def _get_control_token(self, token: Token) -> tuple[str, float]:
        """PIN -> control token for remote commands.  CN-UNVERIFIED.

        The old ``USER_API_URL + "pin?token="`` path is confirmed to still
        exist in the current login-page bundle (``PIN = /api/v1/user/pin``);
        ``/user/profile/pin`` also appears in the binary.  Response keys
        (controlToken / expiresTime) are carried over from the old code.
        """
        if (
            token.control_token is not None
            and token.control_token_expiry > dt.datetime.now().timestamp()
        ):
            return token.control_token, token.control_token_expiry
        if not token.pin:
            raise UnsupportedControlError(
                "A PIN is required for remote control actions on China accounts."
            )
        url = self.USER_API_URL + "pin"
        headers = {
            "Authorization": token.access_token,
            "ccsp-service-id": self.CCSP_SERVICE_ID,
            "ccsp-application-id": self.APP_ID,
            "ccsp-device-id": token.device_id,
            "Content-type": "application/json",
            "Host": self.BASE_URL,
            "Accept-Encoding": "gzip",
            "User-Agent": USER_AGENT_BLUELINK_CN,
        }
        data = {"deviceId": token.device_id, "pin": token.pin}
        response = self.session.put(url, json=data, headers=headers).json()
        _LOGGER.debug(f"{DOMAIN} - Get Control Token Response: {response}")
        if response.get("controlToken") is None:
            raise APIError("PIN verification failed, ensure PIN is entered correctly.")
        control_token = "Bearer " + response["controlToken"]
        control_token_expire_at = math.floor(
            dt.datetime.now().timestamp() + response.get("expiresTime", 0)
        )
        token.control_token = control_token
        token.control_token_expiry = control_token_expire_at
        return control_token, control_token_expire_at

    # ------------------------------------------------------------------
    # Login (fully re-verified against live servers)
    # ------------------------------------------------------------------
    def _get_device_id(self, stamp: str | None = None) -> str:
        """Register a (pseudo) push device and return the server deviceId.

        DIFFERENCE vs old implementation: ``pushType`` is now ``APNS`` (the
        China app uses the APNs + Alibaba push stack, not GCM) and
        ``providerDeviceId`` is mandatory --without it the server answers
        resCode 4002 "service problem".
        """
        registration_id = uuid.uuid4().hex
        provider_device_id = str(uuid.uuid4())
        url = self.SPA_API_URL + "notifications/register"
        payload = {
            "providerDeviceId": provider_device_id,
            "pushRegId": registration_id,
            "pushType": "APNS",
            "uuid": str(uuid.uuid4()),
        }
        headers = {
            "ccsp-service-id": self.CCSP_SERVICE_ID,
            "ccsp-application-id": self.APP_ID,
            "Content-Type": "application/json;charset=UTF-8",
            "Host": self.BASE_URL,
            "Connection": "Keep-Alive",
            "Accept-Encoding": "gzip",
            "User-Agent": USER_AGENT_BLUELINK_CN,
        }
        response = self.session.post(url, headers=headers, json=payload).json()
        _LOGGER.debug(f"{DOMAIN} - Get Device ID request: {headers} {payload}")
        _LOGGER.debug(f"{DOMAIN} - Get Device ID response: {response}")
        if response.get("retCode") == "F":
            raise APIError(f"Device registration failed: {response.get('resMsg')}")
        device_id = response["resMsg"]["deviceId"]
        return device_id

    @staticmethod
    def _uars_state_param(device_uuid: str, interface_id: str) -> str:
        """Build the base64url ``state`` blob the UARS callback expects."""
        blob = {
            "interfaceId": interface_id,
            "accUnqNo": "",
            "deviceUuid": device_uuid,
            "webRedirect": "",
        }
        return (
            base64.urlsafe_b64encode(json.dumps(blob, separators=(",", ":")).encode())
            .decode()
            .rstrip("=")
        )

    def _exchange_uars_callback(self, redirect_url: str) -> dict:
        """Follow the UARS loginCallback.do redirect and harvest the tokens.

        The UARS server performs the OAuth code exchange server-side and
        returns the token bundle inside the response HTML.
        """
        response = self.session.get(
            redirect_url,
            headers={"User-Agent": USER_AGENT_BLUELINK_CN},
            timeout=60,
        )
        if response.status_code >= 400:
            raise AuthenticationError(
                f"UARS login callback failed with HTTP {response.status_code}"
            )
        return _extract_uars_login_bundle(response.text)

    def login(
        self,
        username: str,
        password: str,
        otp_handler: ty.Callable[[dict], dict] | None = None,
        pin: str | None = None,
    ) -> Token:
        """Full password login.  Five-step live-verified flow (see module
        docstring for the old-vs-new comparison)."""
        device_uuid = str(uuid.uuid4()).upper()

        # Step 1: bootstrap the UARS web session (302s into the authorize flow).
        self.session.get(
            f"{self.UARS_BASE_URL}/join/account/loginInit.do?cocd=H&deviceUuid={device_uuid}&redirect=",
            headers={"User-Agent": USER_AGENT_BLUELINK_CN},
            timeout=60,
        )

        # Step 2: authorize with the UARS callback as redirect_uri.  This is
        # the single most important difference from the old implementation --        # the resulting authorization code belongs to the UARS service, NOT to
        # /api/v1/user/oauth2/token (which is why the old flow got errCode
        # 4002 "Invalid parameters").
        state = self._uars_state_param(device_uuid, "UARS-COM-040")
        self.session.get(
            f"https://{self.BASE_URL}/api/v1/user/oauth2/authorize?response_type=code"
            f"&client_id={self.CCSP_SERVICE_ID}"
            f"&redirect_uri={self.UARS_BASE_URL}%2Fjoin%2Fccsp%2FloginCallback.do"
            f"&state={state}&lang={self.LANGUAGE}&scope=url.login",
            headers={"User-Agent": USER_AGENT_BLUELINK_CN},
            timeout=60,
        )

        # Step 3: credentials.  ``mobileNum`` is new but must be present
        # (empty string is accepted); the old {email, password} body alone
        # also works today but the app always sends all three.
        response = self.session.post(
            f"https://{self.BASE_URL}/api/v1/user/signin",
            json={"email": username, "password": password, "mobileNum": ""},
            headers={
                "ccsp-service-id": self.CCSP_SERVICE_ID,
                "ccsp-application-id": self.APP_ID,
                "Content-Type": "application/json;charset=UTF-8",
                "User-Agent": USER_AGENT_BLUELINK_CN,
            },
            timeout=60,
        )
        if response.status_code >= 400:
            raise AuthenticationError(f"Login failed: HTTP {response.status_code}")
        response_json = response.json()
        redirect_url = response_json.get("redirectUrl")
        if not redirect_url:
            raise AuthenticationError(
                "Login failed: no redirectUrl in signin response "
                "(check credentials, or the account requires SMS/WeChat login)"
            )

        # Step 4: the UARS server redeems the code and hands back the tokens.
        bundle = self._exchange_uars_callback(redirect_url)
        data = bundle["data"]
        ccsp_token = data["ccspToken"]
        profile = data.get("profile", {})
        _LOGGER.debug(
            f"{DOMAIN} - Login OK for {profile.get('email')}, "
            f"expires_in={ccsp_token.get('expiresIn')}"
        )

        # Step 5: register this "device" and get the deviceId used by all
        # business requests (ccsp-device-id header).
        device_id = self._get_device_id()

        self._uars_state[username] = {
            "uars_token": data.get("uarsToken"),
            "token_code": data.get("tokenCode"),
            "profile": profile,
        }

        # DIFFERENCE vs old implementation: LOGIN_TOKEN_LIFETIME (30 days)
        # was fiction --the real access token lives 6 hours (expiresIn
        # 21600), so valid_until now reflects the server value.
        expires_in = int(ccsp_token.get("expiresIn", 21600))
        valid_until = dt.datetime.now(dt.UTC) + dt.timedelta(seconds=expires_in)

        return Token(
            username=username,
            password=password,
            access_token=f"{ccsp_token.get('tokenType', 'Bearer')} {ccsp_token['accessToken']}",
            refresh_token=ccsp_token.get("refresh_token")
            or ccsp_token.get("refreshToken"),
            device_id=device_id,
            valid_until=valid_until,
            pin=pin,
        )

    def refresh_access_token(self, token: Token) -> Token:
        """Refresh via the silent re-login.

        DIFFERENCE vs the inherited implementation: the old
        ``oauth2/token`` + refresh_token grant is dead on the current China
        servers (errCode 4002).  Instead the ccapi session cookie mints a new
        UARS callback code via ``/api/v1/user/silentsignin`` (no password).
        If the session cookie has expired, fall back to the full password
        login --no worse than before.
        """
        try:
            response = self.session.post(
                f"https://{self.BASE_URL}/api/v1/user/silentsignin",
                json={"intUserId": ""},
                headers={
                    "ccsp-service-id": self.CCSP_SERVICE_ID,
                    "ccsp-application-id": self.APP_ID,
                    "Content-Type": "application/json;charset=UTF-8",
                    "User-Agent": USER_AGENT_BLUELINK_CN,
                },
                timeout=60,
            )
            if response.status_code >= 400:
                raise APIError(f"silentsignin HTTP {response.status_code}")
            redirect_url = response.json().get("redirectUrl")
            if not redirect_url:
                raise APIError("silentsignin returned no redirectUrl")
            bundle = self._exchange_uars_callback(redirect_url)
            data = bundle["data"]
            ccsp_token = data["ccspToken"]
            expires_in = int(ccsp_token.get("expiresIn", 21600))
            _LOGGER.debug(f"{DOMAIN} - Access token refreshed via silentsignin")
            if token.username in self._uars_state:
                self._uars_state[token.username].update(
                    {
                        "uars_token": data.get("uarsToken"),
                        "token_code": data.get("tokenCode"),
                        "profile": data.get("profile", {}),
                    }
                )
            return Token(
                username=token.username,
                password=token.password,
                access_token=f"{ccsp_token.get('tokenType', 'Bearer')} {ccsp_token['accessToken']}",
                refresh_token=ccsp_token.get("refresh_token")
                or ccsp_token.get("refreshToken"),
                device_id=token.device_id,
                valid_until=dt.datetime.now(dt.UTC) + dt.timedelta(seconds=expires_in),
                pin=token.pin,
                control_token=token.control_token,
                control_token_expiry=token.control_token_expiry,
            )
        except Exception as e:  # fmt: skip
            _LOGGER.warning(
                f"{DOMAIN} - Silent refresh failed ({e}), falling back to full login"
            )
        if token.password:
            return self.login(token.username, token.password, pin=token.pin)
        raise AuthenticationError(
            "Token refresh failed and no stored password is available."
        )

    # ------------------------------------------------------------------
    # Vehicles / state (business surface --same schema as the old code)
    # ------------------------------------------------------------------
    def get_vehicles(self, token: Token) -> list[Vehicle]:
        url = self.SPA_API_URL + "vehicles"
        response = self.session.get(
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
                # DIFFERENCE vs ApiImplType1.get_vehicles: the China vehicle
                # list does not include ccuCCS2ProtocolSupport (verified on a
                # 2025 Custo) --default to 0 so the legacy status path is used.
                ccu_ccs2_protocol_support=entry.get("ccuCCS2ProtocolSupport", 0),
            )
            result.append(vehicle)
        return result

    def update_vehicle_with_cached_state(self, token: Token, vehicle: Vehicle) -> None:
        state = self._get_cached_vehicle_state(token, vehicle)
        self._update_vehicle_properties(vehicle, state)

        if vehicle.engine_type == ENGINE_TYPES.EV:
            try:
                state = self._get_driving_info(token, vehicle)
            except Exception as e:
                # we don't know if all car types (ex: ICE cars) provide this
                # information. We also don't know what the API returns if the
                # info is unavailable. So, catch any exception and move on.
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
        is_ccs2 = vehicle.ccu_ccs2_protocol_support != 0
        if is_ccs2:
            self._force_refresh_vehicle_state_ccs2(token, vehicle)
        else:
            state = self._get_forced_vehicle_state(token, vehicle)
            state["vehicleLocation"] = self._get_location(token, vehicle)
            self._update_vehicle_properties(vehicle, state)
        # Only call for driving info on cars we know have a chance of supporting it.
        if vehicle.engine_type == ENGINE_TYPES.EV:
            try:
                state = self._get_driving_info(token, vehicle)
            except Exception as e:
                _LOGGER.exception(
                    """Failed to parse driving info. Possible reasons:
                                    - new API format
                                    - API outage
                            """,
                    exc_info=e,
                )
            else:
                self._update_vehicle_drive_info(vehicle, state)

    def _force_refresh_vehicle_state_ccs2(self, token: Token, vehicle: Vehicle) -> None:
        # CN-UNVERIFIED for the China region (no CCS2 vehicle in the test
        # account); path mirrors the EU implementation and the /ccs2 strings
        # found in the China app binary.
        url = self.SPA_API_URL + "vehicles/" + vehicle.id + "/ccs2/carstatus/latest"
        response = self.session.get(
            url,
            headers=self._get_authenticated_headers(
                token, vehicle.ccu_ccs2_protocol_support
            ),
            timeout=90,
        ).json()
        _LOGGER.debug(
            f"{DOMAIN} - Force refresh CCS2 vehicle status response: {response}"
        )
        _check_response_for_errors(response)
        state = response["resMsg"]
        self._update_vehicle_properties(vehicle, state)
        location = self._get_location(token, vehicle)
        if location and get_child_value(location, "coord.lat"):
            vehicle.location = (
                get_child_value(location, "coord.lat"),
                get_child_value(location, "coord.lon"),
                parse_datetime(get_child_value(location, "time"), self.data_timezone),
            )

    # The property mapping below is carried over from the previous
    # implementation unchanged: the live /status/latest response (verified on
    # a 2025 Custo) uses exactly the same field layout.
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
        vehicle.car_battery_percentage = normalize_battery_soc(
            get_child_value(state, "status.battery.batSoc")
        )
        vehicle.engine_is_running = get_child_value(state, "status.engine")

        # Converts temp to usable number. Currently only support celsius.
        # "FFH" is the CN sentinel for "no valid temperature" (AC off / not
        # reported) — live-verified on a 2025 Custo.  Its decoded index falls
        # far outside temperature_range, so skip it silently instead of
        # raising IndexError.
        air_temp_value = get_child_value(state, "status.airTemp.value")
        if air_temp_value and air_temp_value != "FFH":
            try:
                tempIndex = get_hex_temp_into_index(air_temp_value)
                vehicle.air_temperature = (
                    self.temperature_range[tempIndex],
                    TEMPERATURE_UNITS[
                        get_child_value(
                            state,
                            "status.airTemp.unit",
                        )
                    ],
                )
            except (ValueError, IndexError):
                _LOGGER.debug(f"{DOMAIN} - Unparsable airTemp value: {air_temp_value}")
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
        vehicle.front_left_seat_status = SEAT_STATUS.get(
            get_child_value(state, "status.seatHeaterVentState.flSeatHeatState")
        )
        vehicle.front_right_seat_status = SEAT_STATUS.get(
            get_child_value(state, "status.seatHeaterVentState.frSeatHeatState")
        )
        vehicle.rear_left_seat_status = SEAT_STATUS.get(
            get_child_value(state, "status.seatHeaterVentState.rlSeatHeatState")
        )
        vehicle.rear_right_seat_status = SEAT_STATUS.get(
            get_child_value(state, "status.seatHeaterVentState.rrSeatHeatState")
        )
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
                "status.evStatus.drvDistance.0.rangeByFuel.totalAvailableRange.value",
            )
            is not None
        ):
            vehicle.total_driving_range = (
                round(
                    float(
                        get_child_value(
                            state,
                            "status.evStatus.drvDistance.0.rangeByFuel.totalAvailableRange.value",
                        )
                    ),
                    1,
                ),
                DISTANCE_UNITS[
                    get_child_value(
                        state,
                        "status.evStatus.drvDistance.0.rangeByFuel.totalAvailableRange.unit",
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
                            "status.evStatus.drvDistance.0.rangeByFuel.evModeRange.value",
                        )
                    ),
                    1,
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
                    "status.evStatus.drvDistance.0.rangeByFuel.gasModeRange.value",
                ),
                DISTANCE_UNITS[
                    get_child_value(
                        state,
                        "status.evStatus.drvDistance.0.rangeByFuel.gasModeRange.unit",
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
                "status.evStatus.reservChargeInfos.targetSOClist.1.dte.rangeByFuel.totalAvailableRange.value",
            ),
            DISTANCE_UNITS[
                get_child_value(
                    state,
                    "status.evStatus.reservChargeInfos.targetSOClist.1.dte.rangeByFuel.totalAvailableRange.unit",
                )
            ],
        )
        vehicle.ev_target_range_charge_DC = (
            get_child_value(
                state,
                "status.evStatus.reservChargeInfos.targetSOClist.0.dte.rangeByFuel.totalAvailableRange.value",
            ),
            DISTANCE_UNITS[
                get_child_value(
                    state,
                    "status.evStatus.reservChargeInfos.targetSOClist.0.dte.rangeByFuel.totalAvailableRange.unit",
                )
            ],
        )
        vehicle.ev_first_departure_enabled = get_child_value(
            state,
            "status.evStatus.reservChargeInfos.reservChargeInfo.reservChargeInfoDetail.reservChargeSet",
        )
        vehicle.ev_second_departure_enabled = get_child_value(
            state,
            "status.evStatus.reservChargeInfos.reserveChargeInfo2.reservChargeInfoDetail.reservChargeSet",
        )
        vehicle.ev_first_departure_days = get_child_value(
            state,
            "status.evStatus.reservChargeInfos.reservChargeInfo.reservChargeInfoDetail.reservInfo.day",
        )
        vehicle.ev_second_departure_days = get_child_value(
            state,
            "status.evStatus.reservChargeInfos.reserveChargeInfo2.reservChargeInfoDetail.reservInfo.day",
        )

        vehicle.ev_first_departure_time = self._get_time_from_string(
            get_child_value(
                state,
                "status.evStatus.reservChargeInfos.reservChargeInfo.reservChargeInfoDetail.reservInfo.time.time",
            ),
            get_child_value(
                state,
                "status.evStatus.reservChargeInfos.reservChargeInfo.reservChargeInfoDetail.reservInfo.time.timeSection",
            ),
        )

        vehicle.ev_second_departure_time = self._get_time_from_string(
            get_child_value(
                state,
                "status.evStatus.reservChargeInfos.reserveChargeInfo2.reservChargeInfoDetail.reservInfo.time.time",
            ),
            get_child_value(
                state,
                "status.evStatus.reservChargeInfos.reserveChargeInfo2.reservChargeInfoDetail.reservInfo.time.timeSection",
            ),
        )

        vehicle.ev_off_peak_start_time = self._get_time_from_string(
            get_child_value(
                state,
                "status.evStatus.reservChargeInfos.offpeakPowerInfo.offPeakPowerTime1.starttime.time",
            ),
            get_child_value(
                state,
                "status.evStatus.reservChargeInfos.offpeakPowerInfo.offPeakPowerTime1.starttime.timeSection",
            ),
        )

        vehicle.ev_off_peak_end_time = self._get_time_from_string(
            get_child_value(
                state,
                "status.evStatus.reservChargeInfos.offpeakPowerInfo.offPeakPowerTime1.endtime.time",
            ),
            get_child_value(
                state,
                "status.evStatus.reservChargeInfos.offpeakPowerInfo.offPeakPowerTime1.endtime.timeSection",
            ),
        )

        if get_child_value(
            state,
            "status.evStatus.reservChargeInfos.offpeakPowerInfo.offPeakPowerFlag",
        ):
            if (
                get_child_value(
                    state,
                    "status.evStatus.reservChargeInfos.offpeakPowerInfo.offPeakPowerFlag",
                )
                == 1
            ):
                vehicle.ev_off_peak_charge_only_enabled = True
            elif (
                get_child_value(
                    state,
                    "status.evStatus.reservChargeInfos.offpeakPowerInfo.offPeakPowerFlag",
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

    def _get_cached_vehicle_state(self, token: Token, vehicle: Vehicle) -> dict:
        url = self.SPA_API_URL + "vehicles/" + vehicle.id + "/status/latest"

        response = self.session.get(
            url, headers=self._get_authenticated_headers(token), timeout=60
        ).json()
        _LOGGER.debug(f"{DOMAIN} - get_cached_vehicle_status response: {response}")
        _check_response_for_errors(response)
        response = response["resMsg"]

        return response

    def _get_location(self, token: Token, vehicle: Vehicle) -> dict:
        url = self.SPA_API_URL + "vehicles/" + vehicle.id + "/location"

        try:
            # The vehicle may need to wake up to acquire a GPS fix --the live
            # test showed >30 s latency, so use a generous timeout.
            response = self.session.get(
                url, headers=self._get_authenticated_headers(token), timeout=90
            ).json()
            _LOGGER.debug(f"{DOMAIN} - _get_location response: {response}")
            _check_response_for_errors(response)
            return response["resMsg"]
        except Exception:
            _LOGGER.debug(f"{DOMAIN} - _get_location failed")
            return None

    def _get_forced_vehicle_state(self, token: Token, vehicle: Vehicle) -> dict:
        # CN-UNVERIFIED: legacy "force refresh" path, presumed intact for
        # non-CCS2 vehicles (the cache endpoint /status/latest is verified).
        url = self.SPA_API_URL + "vehicles/" + vehicle.id + "/status"
        response = self.session.get(
            url, headers=self._get_authenticated_headers(token), timeout=90
        ).json()
        _LOGGER.debug(f"{DOMAIN} - Received forced vehicle data: {response}")
        _check_response_for_errors(response)
        mapped_response = {}
        mapped_response["vehicleStatus"] = response["resMsg"]
        return mapped_response

    # ------------------------------------------------------------------
    # Remote control (paths re-verified in the app binary; header choice
    # follows the inherited Type1 dispatch: legacy vehicles use the access
    # token, CCS2 vehicles use the PIN-derived control token)
    # ------------------------------------------------------------------
    def lock_action(
        self, token: Token, vehicle: Vehicle, action: VEHICLE_LOCK_ACTION
    ) -> str:
        url = self.SPA_API_URL + "vehicles/" + vehicle.id + "/control/door"

        payload = {"action": action.value, "deviceId": token.device_id}
        _LOGGER.debug(f"{DOMAIN} - Lock Action Request: {payload}")
        response = self.session.post(
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
        response = self.session.post(
            url, json=payload, headers=self._get_authenticated_headers(token)
        ).json()
        _LOGGER.debug(f"{DOMAIN} - Charge Port Action Response: {response}")
        _check_response_for_errors(response)
        return response["msgId"]

    def start_climate(
        self, token: Token, vehicle: Vehicle, options: ClimateRequestOptions
    ) -> str:
        url = self.SPA_API_URL + "vehicles/" + vehicle.id + "/control/engine"

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
        response = self.session.post(
            url, json=payload, headers=self._get_authenticated_headers(token)
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
        response = self.session.post(
            url, json=payload, headers=self._get_control_headers(token, vehicle)
        ).json()
        _LOGGER.debug(f"{DOMAIN} - Stop Climate Action Response: {response}")
        _check_response_for_errors(response)
        return response["msgId"]

    def start_charge(self, token: Token, vehicle: Vehicle) -> str:
        url = self.SPA_API_URL + "vehicles/" + vehicle.id + "/control/charge"

        payload = {"action": "start", "deviceId": token.device_id}
        _LOGGER.debug(f"{DOMAIN} - Start Charge Action Request: {payload}")
        response = self.session.post(
            url, json=payload, headers=self._get_authenticated_headers(token)
        ).json()
        _LOGGER.debug(f"{DOMAIN} - Start Charge Action Response: {response}")
        _check_response_for_errors(response)
        return response["msgId"]

    def stop_charge(self, token: Token, vehicle: Vehicle) -> str:
        url = self.SPA_API_URL + "vehicles/" + vehicle.id + "/control/charge"

        payload = {"action": "stop", "deviceId": token.device_id}
        _LOGGER.debug(f"{DOMAIN} - Start Charge Action Request {payload}")
        response = self.session.post(
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
        response = self.session.get(
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
        response = self.session.post(
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

        responseAlltime = self.session.post(
            url,
            json={"periodTarget": 1},
            headers=self._get_authenticated_headers(token),
        )
        responseAlltime = responseAlltime.json()
        _LOGGER.debug(f"{DOMAIN} - get_driving_info responseAlltime {responseAlltime}")
        _check_response_for_errors(responseAlltime)

        response30d = self.session.post(
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
                    distance_unit=vehicle.odometer_unit,
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
                f"{DOMAIN} - Driving info didn't return valid data. This may be normal if the car doesn't support it."
            )
            return None

    def set_charge_limits(
        self, token: Token, vehicle: Vehicle, ac: int, dc: int
    ) -> str:
        # CN-UNVERIFIED: on China the app also exposes a per-current limit
        # (/ccs2/charge/chargingcurrent {"chargingCurrent": N}); the legacy
        # targetSOClist endpoint is kept here until an EV vehicle can confirm.
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
        response = self.session.post(
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
    ) -> ORDER_STATUS:
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
                if state == ORDER_STATUS.PENDING:
                    # state pending: recheck regularly
                    # (until we get a final state or exceed the timeout)
                    sleep(5)
                else:
                    # any other state is final
                    return state

            # if we exit the loop after the set timeout, return a Timeout state
            return ORDER_STATUS.TIMEOUT

        else:
            response = self.session.get(
                url, headers=self._get_authenticated_headers(token)
            ).json()
            _LOGGER.debug(f"{DOMAIN} - Check last action status Response: {response}")
            _check_response_for_errors(response)

            for action in response["resMsg"]:
                if action["recordId"] == action_id:
                    if action["result"] == "success":
                        return ORDER_STATUS.SUCCESS
                    elif action["result"] == "fail":
                        return ORDER_STATUS.FAILED
                    elif action["result"] == "non-response":
                        return ORDER_STATUS.TIMEOUT
                    elif action["result"] is None:
                        _LOGGER.debug(
                            "Action status not set yet by server - try again in a few seconds"
                        )
                        return ORDER_STATUS.PENDING

            # if we iterate the whole notifications list and
            # can't find the action, raise an exception
            raise APIError(f"No action found with ID {action_id}")
