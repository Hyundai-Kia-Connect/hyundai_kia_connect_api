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
    DOMAIN,
    ENGINE_TYPES,
    ORDER_STATUS,
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
    get_index_into_hex_temp,
    parse_datetime,
)
from .Vehicle import (
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

    DRIVING_INFO_ALLTIME_KEY: str = "drivingInfoDetail"

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

    def _get_vehicle_headers(self, token: Token, vehicle: Vehicle) -> dict:
        return self._get_authenticated_headers(token)

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
