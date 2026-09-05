"""GspaApiEU.py — shared EU GSPA base (login CCI + GSPA machinery).

Base class for the EU OneApp (CCI) login flow and the GSPA secure-request
layer.  Brand subclasses (HyundaiCciApiEU, KiaCciApiEU) provide their
constants as class attributes: ONEAPP_CLIENT_ID, ONEAPP_REDIRECT_URI,
CCI_API_URL, CCI_PACKAGE_ID, GSPA_BASE_URL, LOGIN_FORM_HOST, CIPHER_BRAND,
REQUEST_ID_HEADER, DEVICE_ID_HEADER.  Login, token refresh, X-Stamp
computation, and GSPA GET helpers live here.
"""

# pylint:disable=missing-class-docstring,missing-function-docstring,invalid-name,logging-fstring-interpolation,broad-except,too-many-lines

import base64
import datetime as dt
import hashlib
import json
import logging
import re
import uuid
from typing import Any, ClassVar
from urllib.parse import parse_qs, urlparse

import requests
from Crypto.Cipher import PKCS1_v1_5
from Crypto.PublicKey import RSA

from .ApiImpl import (
    ApiImpl,
    ApiImplSession,
    ClimateRequestOptions,
    ScheduleChargingClimateRequestOptions,
    WindowRequestOptions,
)
from .const import (
    BRANDS,
    CHARGE_PORT_ACTION,
    DOMAIN,
    ENGINE_TYPES,
    ORDER_STATUS,
    VALET_MODE_ACTION,
    VEHICLE_LOCK_ACTION,
    WINDOW_STATE,
)
from .exceptions import (
    APIError,
    AuthenticationError,
    ConsentRequiredError,
    DuplicateRequestError,
    InvalidAPIResponseError,
    ServiceTemporaryUnavailable,
    UnsupportedControlError,
)
from .gspa import create_tsid
from .svm import (
    SVMDetails,
    _parse_bool,
    _parse_door_open,
    _parse_int,
    redact_svm_metadata,
)
from .Token import Token
from .utils import float_or_none
from .Vehicle import Vehicle

_LOGGER = logging.getLogger(__name__)

USER_AGENT_OK_HTTP: str = "okhttp/3.12.0"
USER_AGENT_MOZILLA: str = (
    "Mozilla/5.0 (Linux; Android 4.1.1; Galaxy Nexus Build/JRO03C) "
    "AppleWebKit/535.19 (KHTML, like Gecko) Chrome/18.0.1025.166 Mobile Safari/535.19"
)

SUPPORTED_LANGUAGES_LIST = [
    "en",
    "de",
    "fr",
    "it",
    "es",
    "sv",
    "nl",
    "no",
    "cs",
    "sk",
    "hu",
    "da",
    "pl",
    "fi",
    "pt",
]


def _parse_gspa_svm_timestamp(value: Any) -> dt.datetime | None:
    """Parse a GSPA SVM timestamp (media-set ``date`` or ``gpsDetail.time``).

    Shapes seen in the EU response: 14-digit ``yyyyMMddHHmmss`` strings
    (assumed UTC) and epoch-millisecond numbers. ISO 8601 strings are
    accepted as a fallback. Returns None for anything unparsable.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            seconds = int(value)
        except (TypeError, ValueError, OverflowError):
            return None
        if seconds > 1e12:  # epoch ms — checked by magnitude, not by field name
            seconds //= 1000
        try:
            return dt.datetime.fromtimestamp(seconds, tz=dt.UTC)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str):
        if len(value) == 14 and value.isdigit():
            try:
                return dt.datetime.strptime(value, "%Y%m%d%H%M%S").replace(
                    tzinfo=dt.UTC
                )
            except ValueError:
                return None
        try:
            return dt.datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


def parse_svm_detail(detail: dict[str, Any]) -> SVMDetails:
    """Parse a GSPA SVM ``scsDetail`` object into SVMDetails.

    The GSPA response shape mirrors the USA SVM response (gpsDetail with
    coord/head/speed/time, doorOpen, trunkOpen, imageSize). Fields without
    a typed slot on SVMDetails (installAngle, boundaryArea,
    validAngleofView, sideMirrorOpen) stay available through raw_metadata
    with the base64 image redacted.
    """
    gps = detail.get("gpsDetail")
    if not isinstance(gps, dict):
        gps = {}
    speed = gps.get("speed")
    if not isinstance(speed, dict):
        speed = {}
    coord = gps.get("coord")
    if not isinstance(coord, dict):
        coord = {}

    image_b64 = detail.get("svmImage") or ""
    try:
        image_bytes = base64.b64decode(image_b64) if image_b64 else b""
    except (ValueError, TypeError):
        image_bytes = b""

    image_size = None
    image_size_raw = detail.get("imageSize")
    if isinstance(image_size_raw, list) and len(image_size_raw) >= 2:
        width = _parse_int(image_size_raw[0])
        height = _parse_int(image_size_raw[1])
        if width is not None and height is not None:
            image_size = (width, height)

    captured_at_raw = gps.get("time")
    return SVMDetails(
        image_bytes=image_bytes,
        captured_at=_parse_gspa_svm_timestamp(captured_at_raw),
        captured_at_raw=captured_at_raw if isinstance(captured_at_raw, str) else None,
        latitude=float_or_none(coord.get("lat")),
        longitude=float_or_none(coord.get("lon")),
        heading=_parse_int(gps.get("head")),
        speed=(float_or_none(speed.get("value")), speed.get("unit")),
        door_open=_parse_door_open(detail.get("doorOpen")),
        trunk_open=_parse_bool(detail.get("trunkOpen")),
        image_size=image_size,
        # Coordinates are deliberately preserved here (gps=False): the typed
        # fields above already carry them, advanced consumers get the rest.
        raw_metadata=redact_svm_metadata(detail, gps=False),
    )


class GspaApiEU(ApiImpl):
    """Shared EU implementation using the OneApp (CCI) login flow
    and the GSPA secure-request layer."""

    data_timezone = dt.UTC
    supports_valet_mode = True

    # Remote control ships per brand only after live verification of the
    # GSPA command layer on that brand. Subclasses flip this to True.
    GSPA_REMOTE_CONTROL_VERIFIED = False

    # Brand placeholders — every subclass MUST override these.
    ONEAPP_CLIENT_ID: str = ""
    ONEAPP_REDIRECT_URI: str = ""
    CCI_API_URL: str = ""
    CCI_PACKAGE_ID: str = ""
    GSPA_BASE_URL: str = ""
    LOGIN_FORM_HOST: str = ""
    CIPHER_BRAND: str = ""
    REQUEST_ID_HEADER: str = ""
    DEVICE_ID_HEADER: str = ""
    CCSP_SERVICE_ID: str = "6d477c38-3ca4-4cf3-9557-2a1929a94654"

    # Library region id (REGIONS enum, e.g. 9 = Europe CCI) is a DIFFERENT
    # namespace from the stamp-region code the SDK cipher expects. EU CCI
    # stamps are computed with the EU stamp region (1), matching the
    # live-verified pre-rework mapping (region 9 -> EU IV).
    STAMP_REGION = 1

    # ------------------------------------------------------------------
    # GSPA control constants (brand-neutral)
    # ------------------------------------------------------------------

    # CCSP endpoint names that differ from their GSPA endpoint names.
    GSPA_ENDPOINT_MAP: ClassVar[dict[str, str]] = {
        "hornlight": "horn-light",
        "windowcurtain": "window-curtain",
    }

    # Endpoints NOT under /gspa/v1/remote/vehicles/{carId}/.
    # (Valet control posts to the "control" endpoint on the valet path —
    # callers pass path_prefix="valet/vehicles" explicitly.)
    GSPA_PATH_PREFIX_MAP: ClassVar[dict[str, str]] = {
        "rearseat-alarm": "safety/vehicles",
    }

    # Endpoints authenticated with standard GSPA headers (bearer) instead of
    # the PIN-derived control token. Everything else is PIN-gated.
    GSPA_BEARER_ENDPOINTS: ClassVar[frozenset[str]] = frozenset(
        {
            "charge-target",
            "charging-current",
            "discharge-limit",
            "charge-alarm",
            "reservation-charge",
            "reservation-hvac",
            "reservation-charge-hvac",
            "reservation-engine",
            "lock-and-start-toggle",
        }
    )

    # Path constant used for action status polling (?path=...).
    GSPA_REMOTE_VEHICLES_PATH = "gspa/v1/remote/vehicles"

    @property
    def CCI_DOMAIN_API_URL(self) -> str:
        return self.CCI_API_URL + "/domain/api/"

    def __init__(self, region: int, brand: int, language: str) -> None:
        super().__init__()

        language = language.lower()
        if len(language) > 2:
            language = language[0:2]
        if language not in SUPPORTED_LANGUAGES_LIST:
            _LOGGER.warning(f"Unsupported language: {language}, fallback to en")
            language = "en"

        self.region: int = region
        self.LANGUAGE: str = language
        self.brand: int = brand

        self._cci_client_name: str = BRANDS[self.brand].lower()
        self._cci_client_version: str = "1.3.3"
        self._cci_client_os_version: str = "18.7"
        self._cci_notification_provider: str = "APNS"

        self.CCSP_API_URL: str = self.GSPA_BASE_URL.rstrip("/")
        if self.CIPHER_BRAND == "hyundai":
            from .gspa.cipher_keys import hyundai_cipher

            self._cipher = hyundai_cipher()
        elif self.CIPHER_BRAND == "kia":
            from .gspa.cipher_keys import kia_cipher

            self._cipher = kia_cipher()
        else:
            raise APIError(f"Unknown cipher brand: {self.CIPHER_BRAND}")

        # PIN-derived control token cache (D5: per API instance, not Token).
        self._control_token: str | None = None
        self._control_token_expiry: float = 0.0

        self.session = ApiImplSession()

    def login(
        self,
        username: str,
        password: str,
        pin: str | None = None,
    ) -> Token:
        """Login via CCI flow and return a Token with all CCI fields.

        Generates a local device_id (UUID), runs the CCI password login,
        registers the device on CCI, and extracts the CCS user-id for
        GSPA X-Stamp computation.
        """
        device_id = str(uuid.uuid4())

        login_result = self._login_with_password(username, password, device_id)

        token = Token(
            username=username,
            password=password,
            access_token=login_result["access_token"],
            refresh_token=login_result["refresh_token"],
            device_id=device_id,
            valid_until=login_result["valid_until"],
            pin=pin,
            cci_access_token=login_result.get("cci_access_token"),
            exchangeable_token=login_result.get("exchangeable_token"),
            exchangeable_refresh_token=login_result.get("exchangeable_refresh_token"),
            non_ccs_token=login_result.get("non_ccs_token"),
            non_ccs_refresh_token=login_result.get("non_ccs_refresh_token"),
            id_token=login_result.get("id_token"),
        )

        # Register device on CCI (non-critical — best effort).
        self._register_device(token)

        # Extract CCS user-id for GSPA X-Stamp (best effort).
        self._fetch_user_id(token)

        return token

    def _login_with_password(
        self, username: str, password: str, device_id: str
    ) -> dict[str, Any]:
        """CCI password login (OneApp client_id, bypasses IDPConnect WAF).

        Confirmed endpoints:
        1. authorize (OneApp client_id, not WAF-blocked)
        2. certs (RSA JWK for password encryption)
        3. signin (RSA-encrypted password, state=ccsp)
        4. token (auth code -> CCI tokens)
        5. token-exchange (CCI -> CCS token)
        """
        host = self.LOGIN_FORM_HOST
        client_id = self.ONEAPP_CLIENT_ID
        redirect_uri = self.ONEAPP_REDIRECT_URI
        mobile_ua = USER_AGENT_MOZILLA + "_CCS_APP_AOS"

        s = ApiImplSession()
        s.headers.update({"User-Agent": mobile_ua})

        # Step 1: authorize
        auth_url = (
            f"{host}/auth/api/v2/user/oauth2/authorize"
            f"?response_type=code&client_id={client_id}"
            f"&redirect_uri={redirect_uri}&lang=en&state=ccsp&country=de"
        )
        auth_resp = s.get(auth_url, allow_redirects=True)
        if "abusing" in auth_resp.text.lower() or "/error?status=400" in auth_resp.url:
            raise AuthenticationError(
                "IDPConnect authorize was blocked by the WAF ('abusing request'). "
                "This is a server-side block, not a credentials problem. See #1273."
            )

        # Step 2: RSA public key
        resp = s.get(f"{host}/auth/api/v1/accounts/certs")
        if resp.status_code != 200:
            raise AuthenticationError(
                f"API error: failed to fetch RSA certs: HTTP {resp.status_code}. "
                "This may indicate an API change."
            )
        jwk = resp.json().get("retValue", {})
        kid = jwk.get("kid", "")
        if not jwk.get("n") or not jwk.get("e"):
            raise AuthenticationError(
                "API error: certs response missing RSA key material"
            )
        n_bytes = base64.urlsafe_b64decode(jwk["n"] + "==")
        e_bytes = base64.urlsafe_b64decode(jwk["e"] + "==")
        key = RSA.construct(
            (int.from_bytes(n_bytes, "big"), int.from_bytes(e_bytes, "big"))
        )
        encrypted_pw = PKCS1_v1_5.new(key).encrypt(password.encode("utf-8")).hex()

        # Step 3: signin with RSA-encrypted password
        resp = s.post(
            f"{host}/auth/account/signin",
            data={
                "client_id": client_id,
                "encryptedPassword": "true",
                "password": encrypted_pw,
                "redirect_uri": redirect_uri,
                "scope": "",
                "nonce": "",
                "state": "ccsp",
                "username": username,
                "connector_session_key": "",
                "kid": kid,
                "_csrf": "",
            },
            allow_redirects=False,
        )
        if resp.status_code != 302:
            raise AuthenticationError(
                f"Signin failed: HTTP {resp.status_code} — {resp.text[:300]}. "
                "Check username and password."
            )
        location = resp.headers.get("location", "")
        code_list = parse_qs(urlparse(location).query).get("code")
        if not code_list:
            if "error" in location.lower():
                error_desc = parse_qs(urlparse(location).query).get(
                    "error_description", ["unknown"]
                )[0]
                raise AuthenticationError(
                    f"Authentication rejected: {error_desc}. "
                    "Check username and password."
                )
            if "/web/v1/user/authorization" in location:
                raise ConsentRequiredError(
                    "Account consent is required. Please log in via a browser "
                    "once to accept the terms, then retry."
                )
            if "authorize" in location:
                raise AuthenticationError(
                    "Authentication failed — returned to login page. "
                    "Check username and password."
                )
            raise AuthenticationError(
                f"API error: unexpected redirect after signin: {location[:250]}"
            )
        code = code_list[0]

        # Step 4: exchange auth code for CCI tokens
        cci = self._exchange_auth_code_for_cci_tokens(device_id, code)
        cci_access_token = cci.get("accessToken", "")
        cci_refresh_token = cci.get("refreshToken", "")
        non_ccs_token = cci.get("nonCcsToken", "")
        exchangeable_token = cci.get("exchangeableAccessToken", "")
        exchangeable_refresh_token = cci.get("exchangeableRefreshToken", "")
        non_ccs_refresh_token = cci.get("nonCcsRefreshToken", "")
        id_token = cci.get("idToken", "")
        cci_expires_in = int(cci.get("expiresIn", 3599))

        # Step 5: exchange CCI token for CCS token
        ccs_token, ccs_valid_until = self._exchange_ccs_token(
            device_id, cci_access_token, non_ccs_token, exchangeable_token
        )

        return {
            "access_token": "Bearer " + ccs_token,
            "refresh_token": cci_refresh_token,
            "expires_in": cci_expires_in,
            "valid_until": ccs_valid_until,
            "cci_access_token": cci_access_token,
            "exchangeable_token": exchangeable_token,
            "exchangeable_refresh_token": exchangeable_refresh_token,
            "non_ccs_token": non_ccs_token,
            "non_ccs_refresh_token": non_ccs_refresh_token,
            "id_token": id_token,
        }

    def _exchange_auth_code_for_cci_tokens(
        self, device_id: str, auth_code: str
    ) -> dict[str, Any]:
        """POST auth code to CCI v1/auth/token (code in URL query, empty body)."""
        headers = self._get_cci_headers(device_id)
        resp = requests.post(
            f"{self.CCI_DOMAIN_API_URL}v1/auth/token",
            params={"code": auth_code},
            headers=headers,
            timeout=(5, 30),
        )
        if resp.status_code != 200:
            raise AuthenticationError(
                f"CCI token exchange failed: HTTP {resp.status_code} — "
                f"{resp.text[:200]}. This may indicate an API change."
            )
        payload: dict[str, Any] = resp.json()
        return payload

    # ------------------------------------------------------------------
    # CCI headers
    # ------------------------------------------------------------------

    def _cci_timezone_offset(self) -> str:
        """Current UTC offset as '+HH:MM'."""
        aware = dt.datetime.now(dt.UTC).astimezone(self.data_timezone)
        off = aware.strftime("%z")
        return f"{off[:3]}:{off[3:]}" if off else "+00:00"

    def _get_cci_headers(
        self,
        device_id: str,
        cci_access_token: str | None = None,
        non_ccs_token: str | None = None,
        exchangeable_token: str | None = None,
        content_type: str | None = None,
    ) -> dict[str, Any]:
        """Headers for the CCI API."""
        headers = {
            "client-id": self.CCI_PACKAGE_ID,
            "client-name": self._cci_client_name,
            "client-version": self._cci_client_version,
            "client-os-code": "ios",
            "client-os-version": self._cci_client_os_version,
            "client-device-id": device_id or "",
            "client-device-model": "iPhone",
            "client-notification-provider-type": self._cci_notification_provider,
            "locale": self.LANGUAGE.upper(),
            "timezone": self._cci_timezone_offset(),
            "Accept": "application/json",
            "Accept-Language": self.LANGUAGE,
            "User-Agent": USER_AGENT_OK_HTTP,
        }
        if non_ccs_token is not None:
            headers["Authentication"] = non_ccs_token
        if cci_access_token is not None:
            cci_access_token = cci_access_token.removeprefix("Bearer ").strip()
            headers["authorization"] = f"Bearer {cci_access_token}"
        if exchangeable_token is not None:
            headers["exchangeable-token"] = exchangeable_token
            headers["non-ccs-token"] = non_ccs_token or ""
        if content_type:
            headers["Content-Type"] = content_type
        else:
            headers["Content-Length"] = "0"
        return headers

    # ------------------------------------------------------------------
    # Vehicle list
    # ------------------------------------------------------------------

    def get_vehicles(self, token: Token) -> list[Vehicle]:
        """Get the list of vehicles from CCI (cci-api-eu, no CCAPI fallback).

        Both brands use the same available-vehicles endpoint shape
        (ccspCarId / ccspVehicle.carId envelope), so the fetch and parser
        are shared.
        """
        url = self.CCI_DOMAIN_API_URL + "v1/vehicle/available-vehicles?detail=true"
        headers = self._get_cci_headers(
            token.device_id or "",
            cci_access_token=token.cci_access_token,
            non_ccs_token=token.non_ccs_token,
            exchangeable_token=token.exchangeable_token,
        )
        response = requests.get(url, headers=headers, timeout=(5, 30))
        if response.status_code != 200:
            raise APIError(
                f"CCI get_vehicles failed: HTTP {response.status_code} — "
                f"{response.text[:200]}"
            )
        data = response.json()
        return self._parse_vehicles_from_cci(data)

    def _parse_vehicles_from_cci(self, data: dict[str, Any]) -> list[Vehicle]:
        vehicles: list[Vehicle] = []
        vehicle_list = (
            data
            if isinstance(data, list)
            else data.get("contents", data.get("vehicles", []))
        )
        if isinstance(vehicle_list, dict):
            vehicle_list = [vehicle_list]

        for entry in vehicle_list:
            ccsp = entry.get("ccspVehicle", {})
            vehicle_id = (
                entry.get("ccspCarId")
                or (ccsp.get("carId") if ccsp else None)
                or entry.get("vehicleId", "")
            )
            ccs2_support = entry.get(
                "ccs2ProtocolSupport", entry.get("ccu_ccs2_protocol_support", 0)
            )
            if not ccs2_support:
                is_ccs = entry.get("isCcs", False)
                is_ccs_open = entry.get("isCcsOpen", False)
                if is_ccs and is_ccs_open:
                    ccs2_support = 2

            car_type = (ccsp.get("carType") if ccsp else "") or ""
            is_ev = entry.get("isEv", False)
            fuel_type = entry.get("fuelType", entry.get("engineFuelCode", ""))
            if is_ev or fuel_type == "EV" or car_type in ("EV", "ELEC"):
                entry_engine_type = ENGINE_TYPES.EV
            elif fuel_type in ("PHEV", "HEV+PHEV") or car_type in ("PHEV",):
                entry_engine_type = ENGINE_TYPES.PHEV
            elif fuel_type == "HEV" or car_type in ("HEV", "HV"):
                entry_engine_type = ENGINE_TYPES.HEV
            else:
                entry_engine_type = ENGINE_TYPES.ICE

            vehicles.append(
                Vehicle(
                    id=vehicle_id,
                    name=entry.get(
                        "vehicleNameView",
                        entry.get("nickname", entry.get("vehicleName", "")),
                    ),
                    model=entry.get("vehicleModelName", entry.get("modelName", "")),
                    VIN=entry.get("vin", ""),
                    timezone=self.data_timezone,
                    engine_type=entry_engine_type,
                    ccu_ccs2_protocol_support=ccs2_support,
                )
            )

        return vehicles

    # ------------------------------------------------------------------
    # CCS token exchange
    # ------------------------------------------------------------------

    def _exchange_ccs_token(
        self,
        device_id: str,
        cci_access_token: str,
        non_ccs_token: str,
        exchangeable_token: str,
    ) -> tuple[str, dt.datetime]:
        """Exchange a CCI access token for a CCS token (token-exchange?serviceType=CCS).

        The CCS token is accepted by GSPA REST endpoints. Returns
        (ccs_token, valid_until).
        """
        headers = self._get_cci_headers(
            device_id,
            cci_access_token=cci_access_token,
            non_ccs_token=non_ccs_token,
            exchangeable_token=exchangeable_token,
        )
        resp = requests.post(
            f"{self.CCI_DOMAIN_API_URL}v1/auth/token-exchange",
            params={"serviceType": "CCS"},
            headers=headers,
            timeout=(5, 30),
        )
        if resp.status_code != 200:
            raise AuthenticationError(
                f"CCS token exchange failed: HTTP {resp.status_code} — "
                f"{resp.text[:200]}. This may indicate an API change."
            )
        data = resp.json()
        ccs_token = data.get("accessToken") or data.get("ccsAccessToken") or ""
        if not ccs_token:
            raise AuthenticationError(
                f"CCS token exchange returned no accessToken: {resp.text[:200]}"
            )
        # expiresTime is the CCS token TTL in seconds (e.g. 86400 = 24h),
        # not an epoch. Treat it as a relative duration from now; fall back to +1h.
        expires_in = data.get("expiresTime")
        if expires_in:
            ccs_valid_until = dt.datetime.now(dt.UTC) + dt.timedelta(
                seconds=int(expires_in)
            )
        else:
            ccs_valid_until = dt.datetime.now(dt.UTC) + dt.timedelta(seconds=3600)
        return ccs_token, ccs_valid_until

    # ------------------------------------------------------------------
    # Device registration (CCI)
    # ------------------------------------------------------------------

    def _register_device(self, token: Token) -> None:
        """Register device on CCI for push notifications.

        Confirmed endpoint: POST /domain/api/v3/notifications/bases/devices
        - appToken: sha256(device_id) — stable across requests
        - deviceToken: device_id (stable UUID)
        """
        url = self.CCI_DOMAIN_API_URL + "v3/notifications/bases/devices"
        headers = self._get_cci_headers(
            token.device_id or "",
            cci_access_token=token.cci_access_token,
            non_ccs_token=token.non_ccs_token,
            exchangeable_token=token.exchangeable_token,
            content_type="application/json",
        )

        device_id = token.device_id or ""
        body = {
            "appToken": hashlib.sha256(device_id.encode()).hexdigest(),
            "deviceModel": "iPhone",
            "deviceAppVer": self._cci_client_version,
            "deviceOsVer": self._cci_client_os_version,
            "deviceToken": device_id,
        }
        try:
            response = requests.post(url, headers=headers, json=body, timeout=(5, 30))
            if response.status_code != 200:
                _LOGGER.debug(
                    f"{DOMAIN} - Device registration failed: HTTP "
                    f"{response.status_code} (non-critical)"
                )
        except Exception:
            _LOGGER.debug(f"{DOMAIN} - Device registration failed (non-critical)")

    # ------------------------------------------------------------------
    # CCS user-id extraction (for GSPA X-Stamp)
    # ------------------------------------------------------------------

    def _fetch_user_id(self, token: Token) -> None:
        """Populate token.user_id for GSPA X-Stamp computation.

        The X-Stamp payload requires the 'uid' claim from the ccs_token JWT.
        Fallback chain:
        1. Extract 'uid' from ccs_token JWT (primary)
        2. Extract 'sub' from id_token JWT (fallback)
        """
        if token.user_id:
            return

        # Primary: uid claim from CCS token JWT
        # The CCS token is stored as access_token (with "Bearer " prefix)
        ccs_token = (token.access_token or "").removeprefix("Bearer ")
        if ccs_token:
            uid = self._extract_jwt_claim(ccs_token, "uid")
            if uid:
                token.user_id = uid
                _LOGGER.debug(f"{DOMAIN} - CCS user ID from ccs_token.uid: {uid}")
                return

        # Fallback: sub from id_token
        if token.id_token:
            sub = self._extract_jwt_claim(token.id_token, "sub")
            if sub:
                token.user_id = sub
                _LOGGER.debug(f"{DOMAIN} - CCS user ID from id_token.sub: {sub}")

    @staticmethod
    def _extract_jwt_claim(jwt_token: str, claim: str) -> str | None:
        """Extract a claim from a JWT without verification."""
        if not jwt_token:
            return None
        jwt_token = jwt_token.removeprefix("Bearer ")
        parts = jwt_token.split(".")
        if len(parts) < 2:
            return None
        try:
            payload_b64 = parts[1]
            payload_b64 += "=" * (4 - len(payload_b64) % 4)
            payload_bytes = base64.b64decode(payload_b64)
            payload = json.loads(payload_bytes)
            value = payload.get(claim)
            return value if isinstance(value, str) else None
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Token refresh
    # ------------------------------------------------------------------

    def refresh_access_token(self, token: Token) -> Token:
        """Refresh access token using the stored CCI token set.

        CCI flow: POST v2/auth/token-refresh with the full token set,
        then re-exchange the CCS token. Falls back to full login if
        the refresh token is missing or the exchange fails.
        """
        if token.cci_access_token or getattr(token, "non_ccs_token", None):
            try:
                return self._refresh_cci_token(token)
            except Exception:
                _LOGGER.warning("CCI token refresh failed, falling back to full login")
                return self.login(token.username, token.password, token.pin)

        # No CCI tokens — fall back to full login
        return self.login(token.username, token.password, token.pin)

    def _refresh_cci_token(self, token: Token) -> Token:
        """Refresh the CCI token set and re-exchange the CCS token.

        POST cci-api-eu/domain/api/v2/auth/token-refresh with the full
        CCI token set (JSON), then re-exchange the CCS token.

        Live probe (2026-09-04, one account): v1+JSON returns
        HTTP 500 code 9009; v1+form-encoded and v2+JSON both return
        HTTP 200 with the full refreshed set (connector, expiresIn,
        isRequiredTerm). v2+JSON adopted — it is the shape confirmed in
        production iOS HAR traffic, and the v1 path is form-encoded in
        the app, not JSON.
        """
        device_id = token.device_id or ""
        headers = self._get_cci_headers(
            device_id,
            cci_access_token=token.cci_access_token,
            non_ccs_token=token.non_ccs_token,
            exchangeable_token=token.exchangeable_token,
            content_type="application/json",
        )
        body = {
            "accessToken": (token.cci_access_token or "").removeprefix("Bearer "),
            "refreshToken": token.refresh_token or "",
            "exchangeableAccessToken": token.exchangeable_token or "",
            "exchangeableRefreshToken": token.exchangeable_refresh_token or "",
            "nonCcsToken": token.non_ccs_token or "",
            "nonCcsRefreshToken": token.non_ccs_refresh_token or "",
            "idToken": token.id_token or "",
        }
        resp = requests.post(
            f"{self.CCI_DOMAIN_API_URL}v2/auth/token-refresh",
            headers=headers,
            json=body,
            timeout=(5, 30),
        )
        if resp.status_code != 200:
            raise AuthenticationError(
                f"CCI token refresh failed: HTTP {resp.status_code} — {resp.text[:200]}"
            )
        data = resp.json()
        cci_access_token = data.get("accessToken", token.cci_access_token or "")
        cci_refresh_token = data.get("refreshToken", token.refresh_token or "")
        non_ccs_token = data.get("nonCcsToken", token.non_ccs_token or "")
        exchangeable_token = data.get(
            "exchangeableAccessToken", token.exchangeable_token or ""
        )
        exchangeable_refresh_token = data.get(
            "exchangeableRefreshToken", token.exchangeable_refresh_token or ""
        )
        non_ccs_refresh_token = data.get(
            "nonCcsRefreshToken", token.non_ccs_refresh_token or ""
        )
        id_token = data.get("idToken", token.id_token or "")

        # set-cookie t= may carry an updated exchangeable token
        set_cookie = resp.headers.get("set-cookie", "")
        if "t=" in set_cookie:
            m = re.search(r"(?:^|;\s*)t=([^;]+)", set_cookie)
            if m and m.group(1):
                exchangeable_token = m.group(1)

        # Re-exchange the CCS token
        ccs_token, ccs_valid_until = self._exchange_ccs_token(
            device_id, cci_access_token, non_ccs_token, exchangeable_token
        )

        return Token(
            username=token.username,
            password=token.password,
            access_token="Bearer " + ccs_token,
            refresh_token=cci_refresh_token,
            device_id=token.device_id,
            valid_until=ccs_valid_until,
            pin=token.pin,
            cci_access_token=cci_access_token,
            exchangeable_token=exchangeable_token,
            exchangeable_refresh_token=exchangeable_refresh_token,
            non_ccs_token=non_ccs_token,
            non_ccs_refresh_token=non_ccs_refresh_token,
            id_token=id_token,
            user_id=token.user_id,
        )

    # ------------------------------------------------------------------
    # Token test
    # ------------------------------------------------------------------

    def test_token(self, token: Token) -> bool:
        """Test if the CCS token is still valid via CCI API."""
        url = self.CCI_DOMAIN_API_URL + "v1/vehicle/available-vehicles?detail=false"
        headers = self._get_cci_headers(
            token.device_id or "",
            cci_access_token=token.cci_access_token,
            non_ccs_token=token.non_ccs_token,
            exchangeable_token=token.exchangeable_token,
        )
        try:
            response = requests.get(url, headers=headers, timeout=(5, 30))
            return bool(response.status_code == 200)
        except Exception:
            _LOGGER.debug(f"{DOMAIN} - CCS token freshness check failed")
            return False

    # ------------------------------------------------------------------
    # GSPA X-Stamp computation
    # ------------------------------------------------------------------

    def _get_stamp(self, token: Token) -> tuple[str, str]:
        """Compute GSPA X-Stamp + tsid for GSPA endpoint authentication.

        Returns (stamp, tsid) — both must be sent as X-Stamp + X-Request-Id
        headers. The server validates the stamp against the tsid.

        Raises APIError if computation fails.
        """
        try:
            device_id = (token.device_id or "").replace("-", "")
            tsid = create_tsid(device_id)
            epoch_seconds = int(dt.datetime.now(dt.UTC).timestamp())
            user_id = token.user_id or ""
            stamp = self._cipher.compute_x_stamp(
                region=self.STAMP_REGION,
                tsid=tsid,
                epoch_seconds=epoch_seconds,
                user_id=user_id,
            )
            return stamp, tsid
        except NotImplementedError:
            raise
        except Exception as e:
            raise APIError(f"X-Stamp computation failed: {e}") from e

    # ------------------------------------------------------------------
    # GSPA authenticated headers
    # ------------------------------------------------------------------

    def _get_authenticated_headers(
        self, token: Token, ccs2_support: int = 0
    ) -> dict[str, Any]:
        """Headers for GSPA REST endpoints on the brand GSPA host."""
        ccs_token = (token.access_token or "").removeprefix("Bearer ")
        headers = {
            "Authorization": f"Bearer {ccs_token}",
            "ccsp-service-id": self.CCSP_SERVICE_ID,
            "ccsp-application-id": self.CCSP_SERVICE_ID,
            "ccsp-device-id": token.device_id or "",
            self.DEVICE_ID_HEADER: token.device_id or "",
            "Ccuccs2protocolsupport": str(ccs2_support),
            "client-id": self.ONEAPP_CLIENT_ID,
            "client-name": self._cci_client_name,
            "client-version": self._cci_client_version,
            "client-os-code": "AOS",
            "client-os-version": "14",
            "Language": self.LANGUAGE,
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT_OK_HTTP,
        }
        stamp, tsid = self._get_stamp(token)
        headers["X-Stamp"] = stamp
        headers[self.REQUEST_ID_HEADER] = tsid
        return headers

    def _validate_ccs_token(self, token: Token) -> None:
        """Ensure the CCS token is still valid for GSPA requests.

        The destination Token stores the CCS token as access_token (with
        'Bearer ' prefix) and its expiry as valid_until. If the token is
        expired, raise AuthenticationError so the caller can refresh.
        """
        if not token.access_token:
            raise AuthenticationError("No CCS token — cannot make GSPA request")
        valid_until = token.valid_until
        if not isinstance(valid_until, dt.datetime):
            return
        if valid_until.tzinfo is None:
            valid_until = valid_until.replace(tzinfo=dt.UTC)
        if valid_until - dt.timedelta(seconds=60) <= dt.datetime.now(dt.UTC):
            raise AuthenticationError("CCS token expired — refresh required")

    def _raise_gspa_error(self, status_code: int, data: dict[str, Any]) -> None:
        """Raise a typed exception from a GSPA failure response.

        Classification (HTTP status / resCode / rc -> typed exception):
          401                       -> AuthenticationError
          "400-004"/"4004"          -> DuplicateRequestError (queued duplicate)
          resCode "403-*"           -> AuthenticationError (stamp/auth failure)
          "no update info" in msg   -> APIError (no pending OTA — business state)
          resCode "404-*"           -> UnsupportedControlError
          5xx HTTP / resCode "5-*"  -> ServiceTemporaryUnavailable
          else                      -> APIError with the raw server message

        Handles three response shapes: the control-command envelope
        ({"rc": ..., "msg": ...}), the REST envelope
        ({"metaInfo": {"resCode": ..., "message": ...}}), and the Spring
        Boot default error body ({"status": 404, "error": "Not Found",
        "message": ...}) emitted when a GSPA route does not exist.
        """
        if status_code == 401:
            raise AuthenticationError("GSPA: token expired or invalid")
        meta: dict[str, Any] = (
            data.get("metaInfo", {}) if isinstance(data, dict) else {}
        )
        # Spring Boot default error body ({"status": 404, "error": "Not
        # Found", "message": "No static resource ...", "path": ...}) — used
        # when a GSPA route does not exist for this vehicle/server.
        if (
            not meta
            and not data.get("rc")
            and isinstance(data.get("status"), int)
            and data.get("error")
        ):
            spring_code = data["status"]
            spring_msg = data.get("message", "")
            if spring_code == 404:
                raise UnsupportedControlError(
                    f"GSPA not supported: {spring_code} {spring_msg}"
                )
            if spring_code == 403:
                raise AuthenticationError(
                    f"GSPA auth/stamp: {spring_code} {spring_msg}"
                )
            if spring_code >= 500:
                raise ServiceTemporaryUnavailable(
                    f"GSPA transient: {spring_code} {spring_msg}"
                )
            raise APIError(f"GSPA error: rc={spring_code}, msg={spring_msg}")
        res_code = meta.get("resCode") or data.get("rc")
        msg = meta.get("message") or data.get("msg", "")
        if res_code in ("400-004", "4004"):
            raise DuplicateRequestError(f"GSPA duplicate: {res_code} {msg}")
        if isinstance(res_code, str) and res_code.startswith("403"):
            raise AuthenticationError(f"GSPA auth/stamp: {res_code} {msg}")
        if "update info" in str(msg).lower():
            raise APIError(f"No pending OTA update: {res_code} {msg}".strip())
        if isinstance(res_code, str) and res_code.startswith("404"):
            raise UnsupportedControlError(f"GSPA not supported: {res_code} {msg}")
        if status_code >= 500 or (
            isinstance(res_code, str) and res_code.startswith("5")
        ):
            raise ServiceTemporaryUnavailable(f"GSPA transient: {res_code} {msg}")
        raise APIError(f"GSPA error: rc={res_code}, msg={msg}")

    # ------------------------------------------------------------------
    # GSPA control: PIN-derived control token + control commands
    # ------------------------------------------------------------------

    def _get_control_token(self, token: Token) -> tuple[str, int]:
        """Verify the PIN and return (control_token, expiry_epoch_seconds).

        Uses the CCI PIN endpoint (confirmed endpoint shape):
          POST {CCI_DOMAIN_API_URL}v1/auth/pin   body: {"pin": "<pin>"}
        Response: {"isMatched": true, "controlTokenInfo":
                   {"controlToken": "...", "expiresTime": <ttl seconds>}}
        """
        if not token.pin:
            raise UnsupportedControlError(
                "PIN is not configured — remote control requires a PIN"
            )
        url = self.CCI_DOMAIN_API_URL + "v1/auth/pin"
        headers = self._get_cci_headers(
            token.device_id or "",
            cci_access_token=token.cci_access_token,
            non_ccs_token=token.non_ccs_token,
            exchangeable_token=token.exchangeable_token,
            content_type="application/json",
        )
        try:
            response = requests.post(
                url, json={"pin": token.pin}, headers=headers, timeout=(5, 30)
            )
            resp: dict[str, Any] = response.json()
        except ValueError as e:
            raise APIError("CCI PIN endpoint returned a non-JSON body") from e
        if resp.get("isMatched") is not True:
            # 2xx business error (live-probed 2026-09-04: HTTP 200 with
            # isMatched false and controlTokenInfo null). After 5 failed
            # attempts the server locks PIN entry for a window: remainCount
            # drops 4->0 per failure, and while locked even the CORRECT pin
            # returns isMatched false until the window passes. remainTime
            # is the constant window length (SECONDS), not a countdown.
            failed = resp.get("remainCountOnFailedInfo") or {}
            remaining = failed.get("remainCount")
            if remaining == 0:
                window = failed.get("remainTime")
                raise APIError(
                    "PIN is temporarily locked by the server "
                    f"(lockout window: {window}s). Wait for the lockout "
                    "to expire, then the correct PIN will work again."
                )
            if remaining is not None:
                raise APIError(
                    "PIN verification failed, ensure PIN is entered "
                    f"correctly. ({remaining} attempts remaining)"
                )
            raise APIError("PIN verification failed, ensure PIN is entered correctly.")
        info: dict[str, Any] = resp.get("controlTokenInfo", {})
        control_token = info.get("controlToken")
        if not control_token:
            raise InvalidAPIResponseError("CCI PIN response missing controlToken")
        try:
            expires_ms = int(info.get("expiresTime", 0))
        except (TypeError, ValueError) as e:
            raise InvalidAPIResponseError("CCI PIN response missing expiresTime") from e
        # expiresTime semantics live-probed (2026-09-04): a relative TTL in
        # seconds (600 = 10 min) — the same field name the CCS token-exchange
        # and legacy Type1 PIN endpoints use for a TTL. Fall back through
        # ms/seconds epoch timestamps in case the server ever switches
        # (values > 1e12 are implausible as a TTL).
        now = dt.datetime.now(dt.UTC).timestamp()
        if expires_ms > 1e12:
            expire_at = expires_ms // 1000  # ms epoch
        elif expires_ms > 1e9:
            expire_at = expires_ms  # seconds epoch
        else:
            expire_at = int(now) + expires_ms  # TTL seconds
        return f"Bearer {control_token}", expire_at

    def _get_control_token_cached(self, token: Token) -> str:
        """Return the cached control token, verifying the PIN once per cycle."""
        now = dt.datetime.now(dt.UTC).timestamp()
        if self._control_token and now < self._control_token_expiry - 30:
            return self._control_token
        control_token, expire_at = self._get_control_token(token)
        self._control_token = control_token
        self._control_token_expiry = float(expire_at)
        return control_token

    def _invalidate_control_token(self) -> None:
        self._control_token = None
        self._control_token_expiry = 0.0

    def _get_control_headers(self, token: Token, vehicle: Vehicle) -> dict[str, Any]:
        """Headers for PIN-gated GSPA control commands.

        Same base as _get_authenticated_headers, but Authorization carries the
        PIN-derived control token (mirrored in AuthorizationCCSP).
        """
        control_token = self._get_control_token_cached(token)
        headers = self._get_authenticated_headers(
            token, vehicle.ccu_ccs2_protocol_support or 0
        )
        headers["Authorization"] = control_token
        headers["AuthorizationCCSP"] = control_token
        return headers

    def _get_control_request_headers(
        self, token: Token, vehicle: Vehicle, endpoint: str
    ) -> dict[str, Any]:
        """Dispatch request headers by endpoint auth class (bearer vs PIN)."""
        if endpoint in self.GSPA_BEARER_ENDPOINTS:
            return self._get_authenticated_headers(
                token, vehicle.ccu_ccs2_protocol_support or 0
            )
        return self._get_control_headers(token, vehicle)

    def _gspa_control_command(
        self,
        token: Token,
        vehicle: Vehicle,
        endpoint: str,
        body: dict[str, Any],
        path_prefix: str | None = None,
    ) -> str:
        """Send a control command via a GSPA endpoint.

        POST {CCSP_API_URL}/gspa/v1/{prefix}/{carId}/{endpoint}; prefix
        defaults to "remote/vehicles" unless the endpoint map says otherwise.
        Body keys follow the confirmed protocol tables ("command", not
        "action"; no "deviceId").

        Response envelopes (standardized shape live-probed 2026-09-05):
        success is {"data": {...}, "metaInfo": {"retCode": "S",
        "resCode": "202-000"}} where "data" (CarRemoteControlApiResponse)
        carries SID as the primary polling handle and svcSID as the
        alternate; the legacy {"rt", "rc", "rs"} keys stay as a fallback.
        Returns "gspa:{SID}" for action status polling — or the bare
        "gspa:" when the command is accepted with an empty "data" object
        (no polling handle). On a 401 for a PIN-gated endpoint the control
        token cache is invalidated and the command is retried exactly once.

        Pre-CCS2 EU vehicles are rejected with UnsupportedControlError
        (region 1 handles them), and brands with
        GSPA_REMOTE_CONTROL_VERIFIED=False raise NotImplementedError before
        any request is sent.
        """
        if not self.GSPA_REMOTE_CONTROL_VERIFIED:
            raise NotImplementedError(
                f"{self.__class__.__name__} GSPA remote control awaits "
                "live verification"
            )
        if not vehicle.ccu_ccs2_protocol_support:
            raise UnsupportedControlError(
                "Pre-CCS2 EU vehicles are not supported by the CCI region — "
                "use region 1 (Europe) for remote control"
            )
        gspa_endpoint = self.GSPA_ENDPOINT_MAP.get(endpoint, endpoint)
        prefix = path_prefix or self.GSPA_PATH_PREFIX_MAP.get(
            endpoint, "remote/vehicles"
        )
        # Normalize legacy bodies: GSPA uses "command"; no deviceId.
        if "action" in body and "command" not in body:
            action_value = body["action"]
            body = {k: v for k, v in body.items() if k not in ("action", "deviceId")}
            body["command"] = action_value
        body = {k: v for k, v in body.items() if k not in ("action", "deviceId")}

        url = self.CCSP_API_URL + f"/gspa/v1/{prefix}/{vehicle.id}/{gspa_endpoint}"
        self._validate_ccs_token(token)
        pin_gated = endpoint not in self.GSPA_BEARER_ENDPOINTS
        response: requests.Response | None = None
        for attempt in (1, 2):
            headers = self._get_control_request_headers(token, vehicle, endpoint)
            response = requests.post(url, headers=headers, json=body, timeout=(5, 30))
            if response.status_code == 401 and pin_gated and attempt == 1:
                self._invalidate_control_token()
                continue
            break
        assert response is not None  # loop always runs at least once

        if response.status_code >= 400:
            try:
                data: dict[str, Any] = response.json()
            except ValueError:
                data = {}
            self._raise_gspa_error(response.status_code, data)
        try:
            data = response.json()
        except ValueError as e:
            raise InvalidAPIResponseError(
                f"GSPA control returned non-JSON body: {response.text[:200]!r}"
            ) from e
        if not isinstance(data, dict):
            raise InvalidAPIResponseError("GSPA control returned non-object JSON")
        # Standardized envelope (live-probed 2026-09-05): a successful
        # command returns {"data": {...}, "metaInfo": {"retCode": "S",
        # "resCode": "202-000", "msgId": ...}}; a 2xx business failure
        # carries retCode "F". Legacy {"rt", "rc", "rs"} keys stay as a
        # fallback.
        meta = data.get("metaInfo")
        meta_payload: dict[str, Any] = meta if isinstance(meta, dict) else {}
        rc = data.get("rc") or meta_payload.get("retCode")
        if rc and rc not in ("0000", "S"):
            self._raise_gspa_error(response.status_code, data)
        rs = data.get("rs")
        rs_payload = rs if isinstance(rs, dict) else {}
        data_payload = data.get("data") if isinstance(data.get("data"), dict) else {}
        # SID is the primary polling handle; svcSID the alternate (some
        # commands return only svcSID). The response DTO
        # (CarRemoteControlApiResponse) sits under "data".
        sid = (
            data_payload.get("SID")
            or data_payload.get("svcSID")
            or data.get("SID")
            or rs_payload.get("SID")
            or data.get("svcSID")
            or rs_payload.get("svcSID")
            or ""
        )
        if not sid:
            # Live-probed 2026-09-05 (rearseat-alarm): some commands are
            # accepted (HTTP 202, retCode "S", resCode "202-000") with an
            # EMPTY "data" object — no SID and no svcSID. The server has
            # accepted the command, so raising here would report a failure
            # for a command that was in fact executed. Return the bare
            # "gspa:" prefix: the action-status dispatcher still routes it,
            # and callers that poll get PENDING until they give up.
            _LOGGER.debug(
                f"{DOMAIN} - GSPA control accepted without a polling SID "
                f"(rc={rc!r}); status polling has no handle"
            )
            return "gspa:"
        return f"gspa:{sid}"

    def _gspa_check_action_status(
        self, token: Token, vehicle: Vehicle, sid: str
    ) -> ORDER_STATUS:
        """Poll a GSPA action's status.

        GET /gspa/v1/status/vehicles/{carId}/update-status
            ?path=gspa/v1/remote/vehicles
        Response: {"metaInfo": {"retCode": "S"}, "data": {"pollingState":
        "WAIT" | "SUCCESS" | "FAILURE" | "TIMEOUT"}}. Any transport/parse
        error or non-success retCode is reported as PENDING (caller re-polls).
        """
        url = (
            self.CCSP_API_URL
            + f"/gspa/v1/status/vehicles/{vehicle.id}/update-status"
            + f"?path={self.GSPA_REMOTE_VEHICLES_PATH}"
        )
        self._validate_ccs_token(token)
        headers = self._get_authenticated_headers(
            token, vehicle.ccu_ccs2_protocol_support or 0
        )
        try:
            response = requests.get(url, headers=headers, timeout=(5, 30))
            # Live (2026-09-04): a successful poll returns HTTP 202
            # (resCode "202-000 Accepted"), not 200 — accept any 2xx.
            if not 200 <= response.status_code < 300:
                return ORDER_STATUS.PENDING
            data: dict[str, Any] = response.json()
            meta: dict[str, Any] = data.get("metaInfo", {})
            if meta.get("retCode") != "S":
                return ORDER_STATUS.PENDING
            payload: dict[str, Any] = data.get("data", {})
            polling_state = payload.get("pollingState", "")
            if polling_state == "SUCCESS":
                return ORDER_STATUS.SUCCESS
            if polling_state == "FAILURE":
                return ORDER_STATUS.FAILED
            if polling_state == "TIMEOUT":
                return ORDER_STATUS.TIMEOUT
        except Exception:
            _LOGGER.debug(
                f"{DOMAIN} - GSPA action status poll failed for SID {sid}",
                exc_info=True,
            )
        return ORDER_STATUS.PENDING

    # ------------------------------------------------------------------
    # GSPA GET helper
    # ------------------------------------------------------------------

    def _gspa_get(
        self,
        token: Token,
        vehicle: Vehicle,
        endpoint: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """GET from a GSPA endpoint (X-Stamp gated).

        GSPA endpoints use the CCSP host + /gspa/v1/ prefix.
        Response envelope: {"data": {...}, "metaInfo": {"retCode": "S", "resCode": "200-000"}}
        Returns the data (domain payload) dict, or None on business errors.
        """
        self._validate_ccs_token(token)
        car_id = vehicle.id
        url = self.CCSP_API_URL + f"/gspa/v1/{endpoint.format(carId=car_id)}"
        headers = self._get_authenticated_headers(
            token, vehicle.ccu_ccs2_protocol_support or 0
        )

        response = requests.get(url, headers=headers, params=params, timeout=(5, 30))
        if response.status_code == 401:
            raise AuthenticationError("GSPA: Token expired or invalid")
        try:
            data: dict[str, Any] = response.json()
        except ValueError:
            raise APIError(
                f"GSPA error: HTTP {response.status_code} "
                f"non-JSON body: {response.text[:200]!r}"
            )
        meta: dict[str, Any] = data.get("metaInfo", {})
        res_code = meta.get("resCode", "")

        if response.status_code == 403:
            raise APIError(f"GSPA auth error: {res_code} {meta.get('message', '')}")
        if response.status_code >= 400:
            raise APIError(f"GSPA error: HTTP {response.status_code} {res_code}")

        ret_code = meta.get("retCode")

        if ret_code != "S":
            _LOGGER.debug(
                f"{DOMAIN} - GSPA GET {endpoint}: {res_code} {meta.get('message', '')}"
            )
            return None

        payload: dict[str, Any] | None = data.get("data")
        return payload

    # ------------------------------------------------------------------
    # GSPA stored-status
    # ------------------------------------------------------------------

    def get_stored_status(
        self, token: Token, vehicle: Vehicle
    ) -> dict[str, Any] | None:
        """Get cached vehicle status from GSPA stored-status endpoint.

        Returns the data dict from the GSPA response, or None on failure.
        The response contains vehicle state in CCS2 nested format
        (Green.BatteryManagement.*, Cabin.HVAC.*, etc.).
        """
        self._validate_ccs_token(token)
        try:
            return self._gspa_get(
                token, vehicle, "status/vehicles/{carId}/stored-status"
            )
        except AuthenticationError:
            raise
        except Exception:
            _LOGGER.debug(f"{DOMAIN} - GSPA stored-status failed")
            return None

    # ------------------------------------------------------------------
    # SVM (Surround View Monitor) — read side
    # ------------------------------------------------------------------

    def get_svm_details(self, token: Token, vehicle: Vehicle) -> SVMDetails:
        """Return the latest stored SVM image and metadata.

        GSPA keeps SVM captures in a media library: GET
        ``svm/vehicles/{carId}/na-images`` lists the stored sets and GET
        ``svm/vehicles/{carId}/na-images/{tvId}`` returns one set's detail.
        This returns the newest set (by ``date``) parsed into SVMDetails.
        """
        self._validate_ccs_token(token)
        list_data = self._gspa_get(token, vehicle, "svm/vehicles/{carId}/na-images")
        items = list_data.get("scsList") if isinstance(list_data, dict) else None
        candidates: list[tuple[dt.datetime, str]] = []
        if isinstance(items, list):
            for item in items:
                if not isinstance(item, dict):
                    continue
                tv_id = item.get("tvid")
                created_at = _parse_gspa_svm_timestamp(item.get("date"))
                if tv_id and created_at is not None:
                    candidates.append((created_at, tv_id))
        if not candidates:
            raise APIError("No SVM media available for this vehicle")

        _, latest_tv_id = max(candidates)
        detail_data = self._gspa_get(
            token, vehicle, f"svm/vehicles/{{carId}}/na-images/{latest_tv_id}"
        )
        scs_detail = (
            detail_data.get("scsDetail") if isinstance(detail_data, dict) else None
        )
        if not isinstance(scs_detail, dict):
            raise InvalidAPIResponseError("SVM media detail: missing scsDetail")
        _LOGGER.debug(
            f"{DOMAIN} - get_svm_details response: {redact_svm_metadata(scs_detail)}"
        )
        return parse_svm_detail(scs_detail)
    # Remote control (GSPA) — dispatcher
    # ------------------------------------------------------------------

    def check_action_status(
        self,
        token: Token,
        vehicle: Vehicle,
        action_id: str,
        synchronous: bool = False,
        timeout: int = 0,
    ) -> ORDER_STATUS:
        """Poll the status of a previously issued control action.

        The CCI region only issues "gspa:" action ids; anything else cannot
        be polled here. Brands with GSPA_REMOTE_CONTROL_VERIFIED=False raise
        NotImplementedError (they never issue action ids to poll).
        """
        if not self.GSPA_REMOTE_CONTROL_VERIFIED:
            raise NotImplementedError(
                f"{self.__class__.__name__} GSPA remote control awaits "
                "live verification"
            )
        if action_id.startswith("gspa:"):
            return self._gspa_check_action_status(
                token, vehicle, action_id[len("gspa:") :]
            )
        raise UnsupportedControlError(
            f"Cannot poll action {action_id!r}: the CCI region only issues "
            "'gspa:' action ids"
        )

    # ------------------------------------------------------------------
    # Remote control (GSPA) — simple commands
    # ------------------------------------------------------------------

    def lock_action(
        self, token: Token, vehicle: Vehicle, action: VEHICLE_LOCK_ACTION
    ) -> str:
        command = "close" if action == VEHICLE_LOCK_ACTION.LOCK else "open"
        return self._gspa_control_command(token, vehicle, "door", {"command": command})

    def door_power_off(self, token: Token, vehicle: Vehicle) -> str:
        return self._gspa_control_command(
            token, vehicle, "door-power-off", {"command": "CLOSE"}
        )

    def start_charge(self, token: Token, vehicle: Vehicle) -> str:
        return self._gspa_control_command(
            token, vehicle, "charge", {"command": "start"}
        )

    def stop_charge(self, token: Token, vehicle: Vehicle) -> str:
        return self._gspa_control_command(token, vehicle, "charge", {"command": "stop"})

    def charge_port_action(
        self, token: Token, vehicle: Vehicle, action: CHARGE_PORT_ACTION
    ) -> str:
        command = "open" if action == CHARGE_PORT_ACTION.OPEN else "close"
        return self._gspa_control_command(
            token, vehicle, "portdoor", {"command": command}
        )

    def open_frunk(self, token: Token, vehicle: Vehicle) -> str:
        return self._gspa_control_command(token, vehicle, "frunk", {"command": "open"})

    def start_hazard_lights(self, token: Token, vehicle: Vehicle) -> str:
        return self._gspa_control_command(token, vehicle, "light", {"command": "on"})

    def start_hazard_lights_and_horn(self, token: Token, vehicle: Vehicle) -> str:
        return self._gspa_control_command(
            token, vehicle, "hornlight", {"command": "on"}
        )

    def turn_off_lamp(
        self, token: Token, vehicle: Vehicle, mode: str = "all-off"
    ) -> str:
        return self._gspa_control_command(token, vehicle, "lamp", {"command": mode})

    def start_battery_conditioning(self, token: Token, vehicle: Vehicle) -> str:
        return self._gspa_control_command(
            token, vehicle, "battery-conditioning", {"command": "start"}
        )

    def stop_battery_conditioning(self, token: Token, vehicle: Vehicle) -> str:
        return self._gspa_control_command(
            token, vehicle, "battery-conditioning", {"command": "stop"}
        )

    def stop_rear_seat_alarm(self, token: Token, vehicle: Vehicle) -> str:
        return self._gspa_control_command(
            token,
            vehicle,
            "rearseat-alarm",
            {"command": "stop"},
            path_prefix="safety/vehicles",
        )

    def valet_mode_action(
        self, token: Token, vehicle: Vehicle, action: VALET_MODE_ACTION
    ) -> str:
        """Activate/deactivate valet mode via the valet control endpoint.

        The app posts ValetControlApiRequest{command} to the ``control``
        endpoint on the valet path (ValetRemoteDataSource passes
        getGspaValetVehiclesPath = "gspa/v1/valet/vehicles"):
        POST /gspa/v1/valet/vehicles/{carId}/control.
        """
        command = "activate" if action == VALET_MODE_ACTION.ACTIVATE else "deactivate"
        return self._gspa_control_command(
            token,
            vehicle,
            "control",
            {"command": command},
            path_prefix="valet/vehicles",
        )

    # ------------------------------------------------------------------
    # Remote control (GSPA) — climate / engine / pet care
    # ------------------------------------------------------------------

    @staticmethod
    def _build_seat_climate_info(
        options: ClimateRequestOptions,
    ) -> dict[str, Any] | None:
        """Map ClimateRequestOptions seat fields to the seatClimateInfo shape."""
        info: dict[str, Any] = {}
        if options.front_left_seat is not None:
            info["drvSeatClimateState"] = options.front_left_seat
        if options.front_right_seat is not None:
            info["psgSeatClimateState"] = options.front_right_seat
        if options.rear_left_seat is not None:
            info["rlSeatClimateState"] = options.rear_left_seat
        if options.rear_right_seat is not None:
            info["rrSeatClimateState"] = options.rear_right_seat
        return info if info else None

    def start_climate(
        self, token: Token, vehicle: Vehicle, options: ClimateRequestOptions
    ) -> str:
        body: dict[str, Any] = {"command": "start"}
        if options.set_temp is not None:
            body["hvacTemp"] = str(options.set_temp)
        if options.defrost is not None:
            body["windshieldFrontDefogState"] = options.defrost
        if options.heating is not None:
            body["heating1"] = options.heating
        if options.temp_unit is not None:
            body["tempUnit"] = options.temp_unit
        if options.hvac_temp_type is not None:
            body["hvacTempType"] = options.hvac_temp_type
        if options.driver_seat_location is not None:
            body["drvSeatLoc"] = options.driver_seat_location
        if options.duration is not None:
            body["ignitionDuration"] = options.duration
        if options.steering_wheel is not None:
            body["strgWhlHeating"] = options.steering_wheel
        if options.side_rear_mirror_heating is not None:
            body["sideRearMirrorHeating"] = options.side_rear_mirror_heating
        seat_info = self._build_seat_climate_info(options)
        if seat_info:
            body["seatClimateInfo"] = seat_info
        return self._gspa_control_command(token, vehicle, "temperature", body)

    def stop_climate(self, token: Token, vehicle: Vehicle) -> str:
        return self._gspa_control_command(
            token, vehicle, "temperature", {"command": "stop"}
        )

    def start_engine(
        self,
        token: Token,
        vehicle: Vehicle,
        options: ClimateRequestOptions | None = None,
    ) -> str:
        """Remote start via the engine endpoint.

        Accepts the same climate fields as start_climate plus hvacCtrl
        (options.climate) — confirmed endpoint shape.
        """
        body: dict[str, Any] = {"command": "start"}
        if options:
            if options.set_temp is not None:
                body["hvacTemp"] = str(options.set_temp)
            if options.defrost is not None:
                body["windshieldFrontDefogState"] = options.defrost
            if options.climate is not None:
                body["hvacCtrl"] = 1 if options.climate else 0
            if options.heating is not None:
                body["heating1"] = options.heating
            if options.temp_unit is not None:
                body["tempUnit"] = options.temp_unit
            if options.hvac_temp_type is not None:
                body["hvacTempType"] = options.hvac_temp_type
            if options.driver_seat_location is not None:
                body["drvSeatLoc"] = options.driver_seat_location
            if options.duration is not None:
                body["ignitionDuration"] = options.duration
            if options.steering_wheel is not None:
                body["strgWhlHeating"] = options.steering_wheel
            if options.side_rear_mirror_heating is not None:
                body["sideRearMirrorHeating"] = options.side_rear_mirror_heating
            seat_info = self._build_seat_climate_info(options)
            if seat_info:
                body["seatClimateInfo"] = seat_info
        return self._gspa_control_command(token, vehicle, "engine", body)

    def stop_engine(self, token: Token, vehicle: Vehicle) -> str:
        return self._gspa_control_command(token, vehicle, "engine", {"command": "stop"})

    def start_pet_care(
        self,
        token: Token,
        vehicle: Vehicle,
        options: ClimateRequestOptions | None = None,
    ) -> str:
        # tempUnit values ported verbatim from the confirmed protocol tables —
        # awaiting live validation.
        temp = options.set_temp if options and options.set_temp else 21
        body = {"hvacTemp": str(temp), "tempUnit": "F"}
        return self._gspa_control_command(token, vehicle, "pet-care", body)

    def stop_pet_care(self, token: Token, vehicle: Vehicle) -> str:
        body = {"hvacTemp": "21", "tempUnit": "C"}
        return self._gspa_control_command(token, vehicle, "pet-care", body)

    # ------------------------------------------------------------------
    # Remote control (GSPA) — windows
    # ------------------------------------------------------------------

    def set_windows_state(
        self, token: Token, vehicle: Vehicle, options: WindowRequestOptions
    ) -> str:
        """Set window state via the scope-based window-curtain endpoint.

        GSPA supports scope commands only (all windows or front windows);
        a mixed per-window request raises UnsupportedControlError.
        """
        if not self.supports_window_control:
            raise APIError("Window control not supported")
        drv = options.driver_seat_window
        psg = options.passenger_seat_window
        rl = options.rear_left_window
        rr = options.rear_right_window
        seats = (drv, psg, rl, rr)
        if (
            all(s is None for s in seats)
            and options.rear_left_curtain is None
            and options.rear_right_curtain is None
        ):
            raise UnsupportedControlError("No window state requested")
        front = (drv, psg)
        command: str | None = None
        if all(s == WINDOW_STATE.CLOSED for s in seats):
            command = "window-close"
        elif all(s == WINDOW_STATE.OPEN for s in seats):
            command = "window-open"
        elif all(s == WINDOW_STATE.VENTILATION for s in seats):
            command = "vent"
        elif rl is None and rr is None:
            if all(s == WINDOW_STATE.CLOSED for s in front):
                command = "front-close"
            elif all(s == WINDOW_STATE.OPEN for s in front):
                command = "front-open"
            elif all(s == WINDOW_STATE.VENTILATION for s in front):
                command = "front-vent"
        if command is None:
            raise UnsupportedControlError(
                "Mixed per-window state is not supported via GSPA — use "
                "set_window_curtain for per-seat windows/curtains"
            )
        front_val = drv.value if drv is not None else None
        rear_val = (
            front_val if command in ("window-close", "window-open", "vent") else None
        )
        body: dict[str, Any] = {
            "command": command,
            "drvSeatWindow": front_val,
            "psgSeatWindow": front_val,
            "rlSeatWindow": rear_val,
            "rrSeatWindow": rear_val,
            "rlSeatWindowCurtain": None,
            "rrSeatWindowCurtain": None,
            "drvSeatLoc": options.driver_seat_location,
        }
        return self._gspa_control_command(token, vehicle, "windowcurtain", body)

    def set_window_curtain(
        self, token: Token, vehicle: Vehicle, options: WindowRequestOptions
    ) -> str:
        """Set per-seat windows/curtains via the window-curtain endpoint.

        Values: 0 = close, 1 = open, 2 = vent (WINDOW_STATE IntEnum).
        """
        body: dict[str, Any] = {"command": "open"}
        if options.driver_seat_window is not None:
            body["drvSeatWindow"] = options.driver_seat_window.value
        if options.passenger_seat_window is not None:
            body["psgSeatWindow"] = options.passenger_seat_window.value
        if options.rear_left_window is not None:
            body["rlSeatWindow"] = options.rear_left_window.value
        if options.rear_right_window is not None:
            body["rrSeatWindow"] = options.rear_right_window.value
        if options.rear_left_curtain is not None:
            body["rlSeatWindowCurtain"] = options.rear_left_curtain.value
        if options.rear_right_curtain is not None:
            body["rrSeatWindowCurtain"] = options.rear_right_curtain.value
        if options.driver_seat_location is not None:
            body["drvSeatLoc"] = options.driver_seat_location
        return self._gspa_control_command(token, vehicle, "window-curtain", body)

    # ------------------------------------------------------------------
    # Remote control (GSPA) — charge settings and reservations (bearer)
    # ------------------------------------------------------------------

    def set_charge_limits(
        self, token: Token, vehicle: Vehicle, ac: int, dc: int
    ) -> str:
        body = {
            "targetSOClist": [
                {"plugType": 0, "targetSOClevel": int(dc)},
                {"plugType": 1, "targetSOClevel": int(ac)},
            ],
            "command": "set",
        }
        return self._gspa_control_command(token, vehicle, "charge-target", body)

    def set_charging_current(self, token: Token, vehicle: Vehicle, level: int) -> str:
        body = {"chargingCurrent": level, "command": "set"}
        return self._gspa_control_command(token, vehicle, "charging-current", body)

    def set_vehicle_to_load_discharge_limit(
        self, token: Token, vehicle: Vehicle, limit: int
    ) -> str:
        body = {"dischargingLimit": int(limit), "command": "set"}
        return self._gspa_control_command(token, vehicle, "discharge-limit", body)

    def set_charge_alarm(self, token: Token, vehicle: Vehicle, enabled: bool) -> str:
        if enabled:
            body = {
                "alarmOff": 0,
                "alarmBefore10": 1,
                "alarmBefore20": 1,
                "alarmBefore30": 1,
                "command": "set",
            }
        else:
            body = {
                "alarmOff": 1,
                "alarmBefore10": 0,
                "alarmBefore20": 0,
                "alarmBefore30": 0,
                "command": "set",
            }
        return self._gspa_control_command(token, vehicle, "charge-alarm", body)

    def schedule_reservation_charge(
        self,
        token: Token,
        vehicle: Vehicle,
        options: ScheduleChargingClimateRequestOptions,
    ) -> str:
        """Schedule standalone charging reservation (flat DTO shape)."""
        if options.first_departure is None:
            options.first_departure = (
                ScheduleChargingClimateRequestOptions.DepartureOptions()
            )
        if options.first_departure.time is None:
            options.first_departure.time = dt.time()

        def _make_time(t: dt.time) -> dict[str, Any]:
            return {
                "time": t.strftime("%I%M"),
                "timeSection": 1 if t >= dt.time(12, 0) else 0,
            }

        body = {
            "reservFlag": 1 if options.charging_enabled else 0,
            "offpeakPowerFlag": 2 if options.off_peak_charge_only_enabled else 1,
            "reservStartTime": _make_time(options.off_peak_start_time or dt.time()),
            "reservEndTime": _make_time(
                options.off_peak_end_time or options.off_peak_start_time or dt.time()
            ),
            "command": "set",
        }
        return self._gspa_control_command(token, vehicle, "reservation-charge", body)

    def schedule_reservation_hvac(
        self,
        token: Token,
        vehicle: Vehicle,
        options: ScheduleChargingClimateRequestOptions,
    ) -> str:
        """Schedule standalone HVAC reservation (reservedHVACInfo1/2 shape)."""
        if options.first_departure is None:
            options.first_departure = (
                ScheduleChargingClimateRequestOptions.DepartureOptions()
            )
        if options.first_departure.time is None:
            options.first_departure.time = dt.time()
        if options.second_departure is None:
            options.second_departure = (
                ScheduleChargingClimateRequestOptions.DepartureOptions()
            )
        if options.second_departure.time is None:
            options.second_departure.time = dt.time()
        if options.temperature is None:
            options.temperature = 21.0
        if options.temperature_unit is None:
            options.temperature_unit = 0

        temperature: float = options.temperature
        if options.temperature_unit == 0:
            temperature = round(temperature * 2.0) / 2.0
            temperature = max(17.0, min(27.0, temperature))

        def _make_reserv_info(
            dep: ScheduleChargingClimateRequestOptions.DepartureOptions,
        ) -> dict[str, Any]:
            return {
                "scheduleEnable": dep.enabled if dep.enabled is not None else False,
                "day": dep.days or [0],
                "time": dep.time.strftime("%I%M") if dep.time else "1200",
                "windshieldFrontDefogState": options.defrost or False,
                "ignitionDuration": 10,
                "hvacCtrl": 1 if options.climate_enabled else 0,
                "hvacTempType": 1,
                "hvacTemp": f"{temperature:.1f}",
                "tempUnit": options.temperature_unit,
                "drvSeatLoc": "L",
            }

        def _make_hvac_set() -> dict[str, Any]:
            return {
                "airCtrl": 1 if options.climate_enabled else 0,
                "defrost": options.defrost or False,
                "airTemp": {
                    "value": f"{temperature:.1f}",
                    "hvacTempType": 1,
                    "unit": options.temperature_unit,
                },
                "heating1": 0,
                "airPurifierControl": 0,
            }

        body = {
            "reservedHVACInfo1": {
                "reservHVACflag": 1 if options.first_departure.enabled else 0,
                "reservInfo": _make_reserv_info(options.first_departure),
                "reservHVACSet": _make_hvac_set(),
            },
            "reservedHVACInfo2": {
                "reservHVACflag": 1 if options.second_departure.enabled else 0,
                "reservInfo": _make_reserv_info(options.second_departure),
                "reservHVACSet": _make_hvac_set(),
            },
            "command": "set",
        }
        return self._gspa_control_command(token, vehicle, "reservation-hvac", body)

    def schedule_reservation_engine(
        self,
        token: Token,
        vehicle: Vehicle,
        options: ScheduleChargingClimateRequestOptions,
    ) -> str:
        """Schedule ICE engine remote-start reservation (reservInfo/2 shape)."""
        if options.first_departure is None:
            options.first_departure = (
                ScheduleChargingClimateRequestOptions.DepartureOptions()
            )
        if options.first_departure.time is None:
            options.first_departure.time = dt.time()
        if options.second_departure is None:
            options.second_departure = (
                ScheduleChargingClimateRequestOptions.DepartureOptions()
            )
        if options.second_departure.time is None:
            options.second_departure.time = dt.time()
        if options.temperature is None:
            options.temperature = 21.0
        if options.temperature_unit is None:
            options.temperature_unit = 0
        if options.defrost is None:
            options.defrost = False

        temperature: float = options.temperature
        if options.temperature_unit == 0:
            temperature = round(temperature * 2.0) / 2.0
            temperature = max(17.0, min(27.0, temperature))

        def _make_engine_reserv_info(
            dep: ScheduleChargingClimateRequestOptions.DepartureOptions,
        ) -> dict[str, Any]:
            return {
                "scheduleEnable": dep.enabled if dep.enabled is not None else False,
                "day": dep.days or [0],
                "time": dep.time.strftime("%I%M") if dep.time else "1200",
                "windshieldFrontDefogState": options.defrost or False,
                "ignitionDuration": 10,
                "hvacCtrl": 1 if options.climate_enabled else 0,
                "hvacTempType": 1,
                "hvacTemp": f"{temperature:.1f}",
                "tempUnit": options.temperature_unit,
                "drvSeatLoc": "L",
            }

        body = {
            "reservInfo": _make_engine_reserv_info(options.first_departure),
            "reservInfo2": _make_engine_reserv_info(options.second_departure),
        }
        return self._gspa_control_command(token, vehicle, "reservation-engine", body)

    def schedule_charging_and_climate(
        self,
        token: Token,
        vehicle: Vehicle,
        options: ScheduleChargingClimateRequestOptions,
    ) -> str:
        body = self._build_reservation_body(options)
        return self._gspa_control_command(
            token, vehicle, "reservation-charge-hvac", body
        )

    def _build_reservation_body(
        self,
        options: ScheduleChargingClimateRequestOptions,
    ) -> dict[str, Any]:
        """Build the reservation-charge-hvac body from options."""

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

        if options.off_peak_start_time is None:
            options.off_peak_start_time = dt.time()
        if options.off_peak_end_time is None:
            options.off_peak_end_time = options.off_peak_start_time
        if options.off_peak_charge_only_enabled is None:
            options.off_peak_charge_only_enabled = False
        if options.temperature is None:
            options.temperature = 21.0
        if options.temperature_unit is None:
            options.temperature_unit = 0
        if options.defrost is None:
            options.defrost = False

        temperature: float = options.temperature
        if options.temperature_unit == 0:
            temperature = round(temperature * 2.0) / 2.0
            if temperature > 27.0:
                temperature = 27.0
            elif temperature < 17.0:
                temperature = 17.0

        return {
            "reservChargeInfo": {
                f"reservChargeInfo{i + 1}": {
                    "reservChargeSet": departures[i].enabled,
                    "reservInfo": {
                        "day": departures[i].days,
                        "time": {
                            "time": departures[i].time.strftime("%I%M"),
                            "timeSection": (
                                1 if departures[i].time >= dt.time(12, 0) else 0
                            ),
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
            },
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
            "command": "set",
        }

    def lock_and_start_toggle(
        self, token: Token, vehicle: Vehicle, enable: bool = True
    ) -> str:
        body = {"lockAndStartEnable": enable}
        return self._gspa_control_command(token, vehicle, "lock-and-start-toggle", body)
