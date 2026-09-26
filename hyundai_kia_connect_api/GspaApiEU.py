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
    DISTANCE_UNITS,
    DOMAIN,
    ENGINE_TYPES,
    ORDER_STATUS,
    PRESSURE_SCALES,
    SEAT_LOCATION,
    SEAT_STATUS,
    TEMPERATURE_UNITS,
    VALET_MODE_ACTION,
    VEHICLE_LOCK_ACTION,
    WINDOW_STATE,
    PressureUnit,
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
    _parse_float_list,
    _parse_image_sizes,
    _parse_int,
    redact_svm_metadata,
)
from .Token import Token
from .utils import (
    bool_or_none,
    ccs2_reservation_time_or_none,
    float_or_none,
    get_child_value,
    int_or_none,
    normalize_battery_soc,
    parse_datetime,
    pressure_or_none,
)
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
    coord/head/speed/time, doorOpen, trunkOpen, imageSize, and
    validAngleofView). Fields without a typed slot on SVMDetails
    (installAngle, boundaryArea, sideMirrorOpen) stay available through
    raw_metadata with the base64 image redacted.
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

    image_size, image_sizes = _parse_image_sizes(detail.get("imageSize"))

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
        image_sizes=image_sizes,
        valid_angle_of_view=_parse_float_list(detail.get("validAngleofView")),
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

    # Endpoints individually live-verified on this brand while the rest of
    # GSPA remote control stays gated (GSPA_REMOTE_CONTROL_VERIFIED=False).
    # Evidence-mapped: only commands proven against a live vehicle pass the
    # gate; everything else still raises NotImplementedError.
    GSPA_VERIFIED_ENDPOINTS: ClassVar[frozenset[str]] = frozenset()

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

    # resCode -> (exception class, message label), keyed by exact code
    # ("400-004") or 3-char prefix ("403" matches "403-001").
    GSPA_RES_CODE_MAPPING: ClassVar[dict[str, tuple[type[Exception], str]]] = {
        "400-004": (DuplicateRequestError, "GSPA duplicate"),
        "4004": (DuplicateRequestError, "GSPA duplicate"),
        "403": (AuthenticationError, "GSPA auth/stamp"),
        "404": (UnsupportedControlError, "GSPA not supported"),
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
            "reservation-charge-na",
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

        # Control token caching lives on the Token object (control_token /
        # control_token_expiry) — same pattern as ApiImplType1 for the Type1
        # regions.

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
        # Business-state message takes precedence over the resCode mapping:
        # live-probed OTA check returns 404-007 "No update info found by vin"
        # — a normal "nothing pending" state, not an unsupported control.
        if "update info" in str(msg).lower():
            raise APIError(f"No pending OTA update: {res_code} {msg}".strip())
        if isinstance(res_code, str):
            # Exact codes and 3-char prefixes ("403-001") in one mapping,
            # KiaUvoApiCA error_code_mapping style. Range checks that cannot
            # be a dict (>= 500) stay below.
            mapped = self.GSPA_RES_CODE_MAPPING.get(res_code) or (
                self.GSPA_RES_CODE_MAPPING.get(res_code[:3])
                if len(res_code) > 3
                else None
            )
            if mapped is not None:
                exc_class, label = mapped
                raise exc_class(f"{label}: {res_code} {msg}")
        if status_code >= 500 or (
            isinstance(res_code, str) and res_code.startswith("5")
        ):
            raise ServiceTemporaryUnavailable(f"GSPA transient: {res_code} {msg}")
        raise APIError(f"GSPA error: rc={res_code}, msg={msg}")

    # ------------------------------------------------------------------
    # GSPA control: PIN-derived control token + control commands
    # ------------------------------------------------------------------

    def _get_control_token(self, token: Token) -> tuple[str, float]:
        """Verify the PIN and return (control_token, expiry_epoch_seconds).

        The control token is cached on the Token object (control_token /
        control_token_expiry), the same way ApiImplType1 caches the CCS2
        control token for the Type1 regions: a fresh Token (e.g. after a
        re-login) has no control token, so the PIN is verified again, and a
        serialized Token keeps a still-valid control token across restarts.

        Uses the CCI PIN endpoint (confirmed endpoint shape):
          POST {CCI_DOMAIN_API_URL}v1/auth/pin   body: {"pin": "<pin>"}
        Response: {"isMatched": true, "controlTokenInfo":
                   {"controlToken": "...", "expiresTime": <ttl seconds>}}
        """
        now = dt.datetime.now(dt.UTC).timestamp()
        if token.control_token is not None and token.control_token_expiry > now:
            return token.control_token, token.control_token_expiry
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
        if expires_ms > 1e12:
            expire_at = expires_ms // 1000  # ms epoch
        elif expires_ms > 1e9:
            expire_at = expires_ms  # seconds epoch
        else:
            expire_at = int(now) + expires_ms  # TTL seconds
        token.control_token = f"Bearer {control_token}"
        token.control_token_expiry = float(expire_at)
        return token.control_token, token.control_token_expiry

    def _invalidate_control_token(self, token: Token) -> None:
        """Drop the cached control token so the next command re-verifies the
        PIN (401-retry path)."""
        token.control_token = None
        token.control_token_expiry = 0.0

    def _get_control_headers(self, token: Token, vehicle: Vehicle) -> dict[str, Any]:
        """Headers for PIN-gated GSPA control commands.

        Same base as _get_authenticated_headers, but Authorization carries the
        PIN-derived control token (mirrored in AuthorizationCCSP).
        """
        control_token, _ = self._get_control_token(token)
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
        (region 1 handles them), and commands not live-verified on this
        brand (neither GSPA_REMOTE_CONTROL_VERIFIED nor a listing in
        GSPA_VERIFIED_ENDPOINTS) raise NotImplementedError before
        any request is sent.
        """
        if not (
            self.GSPA_REMOTE_CONTROL_VERIFIED
            or endpoint in self.GSPA_VERIFIED_ENDPOINTS
        ):
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
                self._invalidate_control_token(token)
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
    # GSPA prewakeup (brand-neutral; inherited by both EU CCI brands)
    # ------------------------------------------------------------------

    def prewakeup(self, token: Token, vehicle: Vehicle) -> dict[str, Any] | None:
        """Send a prewakeup command to bring the vehicle online.

        GSPA remote paths are brand-global (the path is shared across EU
        CCI brands, issued on the instance's CCSP host). The app always
        sends a body here (PreWakeupApiRequest, Moshi default
        "prewakeup") — not proven required server-side, but it is what
        the app sends, so it is mirrored. Live-confirmed on a Kia EV6
        (2026-09-23): HTTP 202 accepted with the same shape.
        """
        car_id = vehicle.id
        url = self.CCSP_API_URL + f"/gspa/v1/remote/vehicles/{car_id}/prewakeup"
        self._validate_ccs_token(token)
        headers = self._get_authenticated_headers(
            token, vehicle.ccu_ccs2_protocol_support or 0
        )
        try:
            response = requests.post(
                url, headers=headers, json={"action": "prewakeup"}, timeout=(5, 60)
            )
            if response.status_code == 401:
                raise AuthenticationError("GSPA: Token expired or invalid")
            if response.status_code >= 400:
                raise APIError(
                    f"GSPA control error: HTTP {response.status_code} - "
                    f"{response.text[:200]}"
                )
            data: dict[str, Any] = response.json()
            rc = data.get("rc")
            if rc and rc != "0000":
                raise APIError(f"GSPA error: rc={rc}, msg={data.get('msg', '')}")
            rs: dict[str, Any] = data.get("rs", data)
            return rs
        except AuthenticationError:
            raise
        except Exception:
            _LOGGER.debug(f"{DOMAIN} - GSPA prewakeup failed")
            return None

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
    # CCS2 property parser (shared by Hyundai and Kia CCI)
    # ------------------------------------------------------------------

    def _update_vehicle_properties_ccs2(
        self, vehicle: Vehicle, state: dict[str, Any]
    ) -> None:
        if get_child_value(state, "Offset"):
            offset = float(get_child_value(state, "Offset"))
            hours = int(offset)
            minutes = int((offset - hours) * 60)
            vehicle.timezone = dt.timezone(dt.timedelta(hours=hours, minutes=minutes))
        if get_child_value(state, "Date"):
            vehicle.last_updated_at = parse_datetime(
                get_child_value(state, "Date"), vehicle.timezone
            )
        else:
            vehicle.last_updated_at = dt.datetime.now(self.data_timezone)

        vehicle.odometer = (
            get_child_value(state, "Drivetrain.Odometer"),
            DISTANCE_UNITS[1],
        )
        vehicle.car_battery_percentage = normalize_battery_soc(
            get_child_value(state, "Electronics.Battery.Level")
        )
        vehicle.engine_is_running = get_child_value(state, "DrivingReady")

        air_temp = get_child_value(state, "Cabin.HVAC.Row1.Driver.Temperature.Value")
        if air_temp is not None and air_temp != "OFF":
            air_temp_unit = get_child_value(
                state, "Cabin.HVAC.Row1.Driver.Temperature.Unit"
            )
            vehicle.air_temperature = (
                air_temp,
                TEMPERATURE_UNITS.get(air_temp_unit, TEMPERATURE_UNITS[0]),
            )

        outside_temp = get_child_value(state, "Cabin.HVAC.OutsideTemperature.Value")
        outside_temp_unit = get_child_value(state, "Cabin.HVAC.OutsideTemperature.Unit")
        vehicle.outside_temperature = (
            outside_temp,
            TEMPERATURE_UNITS[outside_temp_unit],
        )

        defrost_is_on = get_child_value(state, "Body.Windshield.Front.Defog.State")
        if defrost_is_on in [0, 2]:
            vehicle.defrost_is_on = False
        elif defrost_is_on == 1:
            vehicle.defrost_is_on = True

        steer_wheel_heat = get_child_value(state, "Cabin.SteeringWheel.Heat.State")
        if steer_wheel_heat in [0, 2]:
            vehicle.steering_wheel_heater_is_on = False
        elif steer_wheel_heat == 1:
            vehicle.steering_wheel_heater_is_on = True
        vehicle.steering_wheel_heat_step = int_or_none(
            get_child_value(state, "Cabin.SteeringWheel.Heat.RemoteControl.Step")
        )

        vehicle.windshield_front_heater_is_on = bool_or_none(
            get_child_value(state, "Body.Windshield.Front.Heat.State")
        )

        defrost_rear_is_on = get_child_value(state, "Body.Windshield.Rear.Defog.State")
        if defrost_rear_is_on in [0, 2]:
            vehicle.back_window_heater_is_on = False
        elif defrost_rear_is_on == 1:
            vehicle.back_window_heater_is_on = True

        vehicle.front_left_seat_status = SEAT_STATUS[
            get_child_value(state, "Cabin.Seat.Row1.Driver.Climate.State")
        ]
        vehicle.front_right_seat_status = SEAT_STATUS[
            get_child_value(state, "Cabin.Seat.Row1.Passenger.Climate.State")
        ]
        vehicle.rear_left_seat_status = SEAT_STATUS[
            get_child_value(state, "Cabin.Seat.Row2.Left.Climate.State")
        ]
        vehicle.rear_right_seat_status = SEAT_STATUS[
            get_child_value(state, "Cabin.Seat.Row2.Right.Climate.State")
        ]

        vehicle.front_left_door_is_open = get_child_value(
            state, "Cabin.Door.Row1.Driver.Open"
        )
        vehicle.front_right_door_is_open = get_child_value(
            state, "Cabin.Door.Row1.Passenger.Open"
        )
        vehicle.back_left_door_is_open = get_child_value(
            state, "Cabin.Door.Row2.Left.Open"
        )
        vehicle.back_right_door_is_open = get_child_value(
            state, "Cabin.Door.Row2.Right.Open"
        )

        vehicle.front_left_door_is_locked = (
            not bool(get_child_value(state, "Cabin.Door.Row1.Driver.Lock"))
            if get_child_value(state, "Cabin.Door.Row1.Driver.Lock") is not None
            else None
        )
        vehicle.front_right_door_is_locked = (
            not bool(get_child_value(state, "Cabin.Door.Row1.Passenger.Lock"))
            if get_child_value(state, "Cabin.Door.Row1.Passenger.Lock") is not None
            else None
        )
        vehicle.back_left_door_is_locked = (
            not bool(get_child_value(state, "Cabin.Door.Row2.Left.Lock"))
            if get_child_value(state, "Cabin.Door.Row2.Left.Lock") is not None
            else None
        )
        vehicle.back_right_door_is_locked = (
            not bool(get_child_value(state, "Cabin.Door.Row2.Right.Lock"))
            if get_child_value(state, "Cabin.Door.Row2.Right.Lock") is not None
            else None
        )

        vehicle.is_locked = (
            vehicle.front_left_door_is_locked
            and vehicle.front_right_door_is_locked
            and vehicle.back_left_door_is_locked
            and vehicle.back_right_door_is_locked
        )

        vehicle.hood_is_open = get_child_value(state, "Body.Hood.Open")
        _open = get_child_value(state, "Cabin.Window.Row1.Driver.Open")
        _level = get_child_value(state, "Cabin.Window.Row1.Driver.OpenLevel")
        vehicle.front_left_window_is_open = bool(_open) if _open is not None else None
        if _level and _level > 0 and not _open:
            vehicle.front_left_window_is_open = True  # vented
        _open = get_child_value(state, "Cabin.Window.Row1.Passenger.Open")
        _level = get_child_value(state, "Cabin.Window.Row1.Passenger.OpenLevel")
        vehicle.front_right_window_is_open = bool(_open) if _open is not None else None
        if _level and _level > 0 and not _open:
            vehicle.front_right_window_is_open = True  # vented
        _open = get_child_value(state, "Cabin.Window.Row2.Left.Open")
        _level = get_child_value(state, "Cabin.Window.Row2.Left.OpenLevel")
        vehicle.back_left_window_is_open = bool(_open) if _open is not None else None
        if _level and _level > 0 and not _open:
            vehicle.back_left_window_is_open = True  # vented
        _open = get_child_value(state, "Cabin.Window.Row2.Right.Open")
        _level = get_child_value(state, "Cabin.Window.Row2.Right.OpenLevel")
        vehicle.back_right_window_is_open = bool(_open) if _open is not None else None
        if _level and _level > 0 and not _open:
            vehicle.back_right_window_is_open = True  # vented
        vehicle.sunroof_is_open = (
            bool(get_child_value(state, "Body.Sunroof.Glass.Open"))
            if get_child_value(state, "Body.Sunroof.Glass.Open") is not None
            else None
        )
        vehicle.tire_pressure_rear_left_warning_is_on = bool(
            get_child_value(state, "Chassis.Axle.Row2.Left.Tire.PressureLow")
        )
        vehicle.tire_pressure_front_left_warning_is_on = bool(
            get_child_value(state, "Chassis.Axle.Row1.Left.Tire.PressureLow")
        )
        vehicle.tire_pressure_front_right_warning_is_on = bool(
            get_child_value(state, "Chassis.Axle.Row1.Right.Tire.PressureLow")
        )
        vehicle.tire_pressure_rear_right_warning_is_on = bool(
            get_child_value(state, "Chassis.Axle.Row2.Right.Tire.PressureLow")
        )
        vehicle.tire_pressure_all_warning_is_on = bool(
            get_child_value(state, "Chassis.Axle.Tire.PressureLow")
        )
        _pu_raw = get_child_value(state, "Chassis.Axle.Tire.PressureUnit")
        if _pu_raw is None:
            vehicle.tire_pressure_unit = None
        else:
            try:
                vehicle.tire_pressure_unit = PressureUnit(_pu_raw)
            except ValueError:
                _LOGGER.warning(
                    "%s - Unknown tire PressureUnit %r; tire pressure values ignored",
                    DOMAIN,
                    _pu_raw,
                )
                vehicle.tire_pressure_unit = None
        _scale = PRESSURE_SCALES.get(vehicle.tire_pressure_unit)
        _pfl = pressure_or_none(
            get_child_value(state, "Chassis.Axle.Row1.Left.Tire.Pressure")
        )
        _pfr = pressure_or_none(
            get_child_value(state, "Chassis.Axle.Row1.Right.Tire.Pressure")
        )
        _prl = pressure_or_none(
            get_child_value(state, "Chassis.Axle.Row2.Left.Tire.Pressure")
        )
        _prr = pressure_or_none(
            get_child_value(state, "Chassis.Axle.Row2.Right.Tire.Pressure")
        )
        vehicle.tire_pressure_front_left = (
            round(_pfl * _scale, 1) if _pfl is not None and _scale is not None else None
        )
        vehicle.tire_pressure_front_right = (
            round(_pfr * _scale, 1) if _pfr is not None and _scale is not None else None
        )
        vehicle.tire_pressure_rear_left = (
            round(_prl * _scale, 1) if _prl is not None and _scale is not None else None
        )
        vehicle.tire_pressure_rear_right = (
            round(_prr * _scale, 1) if _prr is not None and _scale is not None else None
        )
        vehicle.trunk_is_open = get_child_value(state, "Body.Trunk.Open")

        # Headlamp / lamp status
        vehicle.headlamp_status = get_child_value(
            state, "Body.Lights.Front.HeadLamp.SystemWarning"
        )
        vehicle.headlamp_left_low = get_child_value(
            state, "Body.Lights.Front.Left.Low.Warning"
        )
        vehicle.headlamp_left_high = get_child_value(
            state, "Body.Lights.Front.Left.High.Warning"
        )
        vehicle.headlamp_left_bifunc = get_child_value(
            state, "Body.Lights.Front.Left.Bifunc.Warning"
        )
        vehicle.headlamp_right_low = get_child_value(
            state, "Body.Lights.Front.Right.Low.Warning"
        )
        vehicle.headlamp_right_high = get_child_value(
            state, "Body.Lights.Front.Right.High.Warning"
        )
        vehicle.headlamp_right_bifunc = get_child_value(
            state, "Body.Lights.Front.Right.Bifunc.Warning"
        )
        vehicle.stop_lamp_left = get_child_value(
            state, "Body.Lights.Rear.Left.StopLamp.Warning"
        )
        vehicle.stop_lamp_right = get_child_value(
            state, "Body.Lights.Rear.Right.StopLamp.Warning"
        )
        vehicle.turn_signal_left_front = get_child_value(
            state, "Body.Lights.Front.Left.TurnSignal.Warning"
        )
        vehicle.turn_signal_right_front = get_child_value(
            state, "Body.Lights.Front.Right.TurnSignal.Warning"
        )
        vehicle.turn_signal_left_rear = get_child_value(
            state, "Body.Lights.Rear.Left.TurnSignal.Warning"
        )
        vehicle.turn_signal_right_rear = get_child_value(
            state, "Body.Lights.Rear.Right.TurnSignal.Warning"
        )
        vehicle.hazard_lights_on = bool_or_none(
            get_child_value(state, "Body.Lights.Hazard.Alert")
        )

        # Drivetrain / ignition state
        vehicle.transmission_condition = get_child_value(
            state, "Drivetrain.Transmission.ParkingPosition"
        )
        vehicle.gear_position = int_or_none(
            get_child_value(state, "Drivetrain.Transmission.GearPosition")
        )
        vehicle.auto_cut_battery_prewarning_on = bool_or_none(
            get_child_value(state, "Electronics.AutoCut.BatteryPreWarning")
        )
        vehicle.ign3 = get_child_value(state, "Electronics.PowerSupply.Ignition3")
        accessory_ign = get_child_value(state, "Electronics.PowerSupply.Ignition1")
        if accessory_ign is not None:
            vehicle.accessory_on = bool(accessory_ign)
        vehicle.remote_ignition = get_child_value(
            state, "Drivetrain.RemoteIgnition.State"
        )
        vehicle.sleep_mode_check = bool_or_none(
            get_child_value(state, "RemoteControl.SleepMode")
        )

        vehicle.ev_battery_percentage = get_child_value(
            state, "Green.BatteryManagement.BatteryRemain.Ratio"
        )
        vehicle.ev_battery_remain = get_child_value(
            state, "Green.BatteryManagement.BatteryRemain.Value"
        )
        vehicle.ev_battery_capacity = get_child_value(
            state, "Green.BatteryManagement.BatteryCapacity.Value"
        )
        # Vehicles that do not report the state of health send 0 rather than
        # omitting the field, so keep the value unknown instead of reporting a
        # battery at 0% health.
        if battery_soh := get_child_value(state, "Green.BatteryManagement.SoH.Ratio"):
            vehicle.ev_battery_soh_percentage = battery_soh
        vehicle.ev_battery_is_plugged_in = get_child_value(
            state, "Green.ChargingInformation.ConnectorFastening.State"
        )
        charging_door_state = get_child_value(state, "Green.ChargingDoor.State")
        if charging_door_state in [0, 2]:
            vehicle.ev_charge_port_door_is_open = False
        elif charging_door_state == 1:
            vehicle.ev_charge_port_door_is_open = True

        dte_total = get_child_value(state, "Drivetrain.FuelSystem.DTE.Total")
        if dte_total is not None:
            vehicle.total_driving_range = (
                float(dte_total),
                DISTANCE_UNITS[
                    get_child_value(state, "Drivetrain.FuelSystem.DTE.Unit")
                ],
            )
        fuel_dte = get_child_value(state, "Drivetrain.FuelSystem.DTE.Fuel")
        if fuel_dte is not None:
            vehicle.fuel_driving_range = (
                float(fuel_dte),
                vehicle.total_driving_range_unit,
            )
        if vehicle.engine_type == ENGINE_TYPES.EV:
            vehicle.ev_driving_range = (
                vehicle.total_driving_range,
                vehicle.total_driving_range_unit,
            )

        vehicle.ev_estimated_current_charge_duration = (
            get_child_value(state, "Green.ChargingInformation.Charging.RemainTime"),
            "m",
        )
        vehicle.ev_estimated_fast_charge_duration = (
            get_child_value(state, "Green.ChargingInformation.EstimatedTime.Quick"),
            "m",
        )
        vehicle.ev_estimated_portable_charge_duration = (
            get_child_value(state, "Green.ChargingInformation.EstimatedTime.ICCB"),
            "m",
        )
        vehicle.ev_estimated_station_charge_duration = (
            get_child_value(state, "Green.ChargingInformation.EstimatedTime.Standard"),
            "m",
        )
        vehicle.ev_charge_limits_ac = get_child_value(
            state, "Green.ChargingInformation.TargetSoC.Standard"
        )
        vehicle.ev_charge_limits_dc = get_child_value(
            state, "Green.ChargingInformation.TargetSoC.Quick"
        )
        vehicle.ev_charging_current = get_child_value(
            state, "Green.ChargingInformation.ElectricCurrentLevel.State"
        )

        # Expected charge window — live payloads carry the unconfigured
        # sentinel (Day 7, Hour 31, Min 63); the time guard maps it to
        # None. Day semantics are unconfirmed, so only the time is exposed.
        expected = get_child_value(state, "Green.ChargingInformation.ExpectedTime")
        if isinstance(expected, dict):
            vehicle.ev_charge_expected_start_time = ccs2_reservation_time_or_none(
                expected.get("StartHour"), expected.get("StartMin")
            )
            vehicle.ev_charge_expected_end_time = ccs2_reservation_time_or_none(
                expected.get("EndHour"), expected.get("EndMin")
            )

        # Charge-complete alarm presets — independent flags; absent block
        # (EV6 fixture) leaves all four None.
        complete_alarm = get_child_value(
            state, "Green.ChargingInformation.Setting.CompleteAlarm"
        )
        if isinstance(complete_alarm, dict):
            vehicle.ev_charge_complete_alarm_before_10min = bool_or_none(
                complete_alarm.get("Before10min")
            )
            vehicle.ev_charge_complete_alarm_before_20min = bool_or_none(
                complete_alarm.get("Before20min")
            )
            vehicle.ev_charge_complete_alarm_before_30min = bool_or_none(
                complete_alarm.get("Before30min")
            )
            vehicle.ev_charge_complete_alarm_off = bool_or_none(
                complete_alarm.get("Off")
            )
        ev_charging_power = get_child_value(
            state, "Green.Electric.SmartGrid.RealTimePower"
        )
        if ev_charging_power is not None:
            vehicle.ev_charging_power = float(ev_charging_power)
        vehicle.ev_v2l_discharge_limit = get_child_value(
            state, "Green.Electric.SmartGrid.VehicleToLoad.DischargeLimitation.SoC"
        )
        vehicle.ev_v2l_discharge_remain_time = int_or_none(
            get_child_value(
                state,
                "Green.Electric.SmartGrid.VehicleToLoad.DischargeLimitation.RemainTime",
            )
        )
        vehicle.ev_v2l_discharge_dte = int_or_none(
            get_child_value(
                state,
                "Green.Electric.SmartGrid.VehicleToLoad.DischargeLimitation.DTE",
            )
        )
        vehicle.ev_v2l_mode = int_or_none(
            get_child_value(state, "Green.Electric.SmartGrid.VehicleToLoad.Mode")
        )
        vehicle.ev_target_range_charge_AC = (
            get_child_value(state, "Green.ChargingInformation.DTE.TargetSoC.Standard"),
            DISTANCE_UNITS[get_child_value(state, "Drivetrain.FuelSystem.DTE.Unit")],
        )
        vehicle.ev_target_range_charge_DC = (
            get_child_value(state, "Green.ChargingInformation.DTE.TargetSoC.Quick"),
            DISTANCE_UNITS[get_child_value(state, "Drivetrain.FuelSystem.DTE.Unit")],
        )
        departure1_enable = get_child_value(
            state, "Green.Reservation.Departure.Schedule1.Enable"
        )
        if departure1_enable is not None:
            vehicle.ev_first_departure_enabled = bool(departure1_enable)
        departure2_enable = get_child_value(
            state, "Green.Reservation.Departure.Schedule2.Enable"
        )
        if departure2_enable is not None:
            vehicle.ev_second_departure_enabled = bool(departure2_enable)

        departure1_hvac_temp = get_child_value(
            state, "Green.Reservation.Departure.Schedule1.HVAC.Temperature.Value"
        )
        if departure1_hvac_temp is not None:
            departure1_unit = get_child_value(
                state, "Green.Reservation.Departure.Schedule1.HVAC.Temperature.Unit"
            )
            vehicle.ev_first_departure_climate_temperature = (
                float(departure1_hvac_temp),
                TEMPERATURE_UNITS.get(departure1_unit, TEMPERATURE_UNITS[0]),
            )
        departure2_hvac_temp = get_child_value(
            state, "Green.Reservation.Departure.Schedule2.HVAC.Temperature.Value"
        )
        if departure2_hvac_temp is not None:
            departure2_unit = get_child_value(
                state, "Green.Reservation.Departure.Schedule2.HVAC.Temperature.Unit"
            )
            vehicle.ev_second_departure_climate_temperature = (
                float(departure2_hvac_temp),
                TEMPERATURE_UNITS.get(departure2_unit, TEMPERATURE_UNITS[0]),
            )

        schedule1_time = get_child_value(
            state, "Green.Reservation.Departure.Schedule1.Time"
        )
        if schedule1_time is not None:
            vehicle.ev_first_departure_time = schedule1_time
        schedule1_days = get_child_value(
            state, "Green.Reservation.Departure.Schedule1.DaysOfWeek"
        )
        if schedule1_days is not None:
            vehicle.ev_first_departure_days = schedule1_days
        schedule1_hvac = get_child_value(
            state, "Green.Reservation.Departure.Schedule1.HVAC"
        )
        if isinstance(schedule1_hvac, dict):
            enable = schedule1_hvac.get("Enable")
            if enable is not None:
                vehicle.ev_first_departure_climate_enabled = bool(enable)
            defrost = schedule1_hvac.get("Defrost")
            if defrost is not None:
                vehicle.ev_first_departure_climate_defrost = bool(defrost)

        schedule2_time = get_child_value(
            state, "Green.Reservation.Departure.Schedule2.Time"
        )
        if schedule2_time is not None:
            vehicle.ev_second_departure_time = schedule2_time
        schedule2_days = get_child_value(
            state, "Green.Reservation.Departure.Schedule2.DaysOfWeek"
        )
        if schedule2_days is not None:
            vehicle.ev_second_departure_days = schedule2_days
        schedule2_hvac = get_child_value(
            state, "Green.Reservation.Departure.Schedule2.HVAC"
        )
        if isinstance(schedule2_hvac, dict):
            enable2 = schedule2_hvac.get("Enable")
            if enable2 is not None:
                vehicle.ev_second_departure_climate_enabled = bool(enable2)
            defrost2 = schedule2_hvac.get("Defrost")
            if defrost2 is not None:
                vehicle.ev_second_departure_climate_defrost = bool(defrost2)

        # Off-peak charging window — flat CCS2-schema OffPeakTime block
        # (live EV6/PV5 GSPA stored-status + Sportage PHEV CCS2 dump; the
        # nested "OffPeakPower.*" variants appear in no captured payload).
        # Mode 0 = off, 2 = target-priority, 3 = time-priority (kia_uvo
        # #1304/#1269 lineage). When the block is absent all fields stay
        # None — do NOT synthesise dt.time(0,0) (phantom midnight window).
        # ccs2_reservation_time_or_none handles the 31:70 sentinel silently.
        off_peak = get_child_value(state, "Green.Reservation.OffPeakTime")
        if off_peak:
            try:
                vehicle.ev_off_peak_start_time = ccs2_reservation_time_or_none(
                    off_peak.get("StartHour"), off_peak.get("StartMin")
                )
                vehicle.ev_off_peak_end_time = ccs2_reservation_time_or_none(
                    off_peak.get("EndHour"), off_peak.get("EndMin")
                )
            except (TypeError, ValueError):
                _LOGGER.warning("%s - CCS2 OffPeakTime malformed: %s", DOMAIN, off_peak)
                vehicle.ev_off_peak_start_time = None
                vehicle.ev_off_peak_end_time = None

            mode = off_peak.get("Mode")
            if mode == 0:
                vehicle.ev_schedule_charge_enabled = False
                vehicle.ev_off_peak_charge_only_enabled = None
            elif mode == 2:
                vehicle.ev_schedule_charge_enabled = True
                vehicle.ev_off_peak_charge_only_enabled = False
            elif mode == 3:
                vehicle.ev_schedule_charge_enabled = True
                vehicle.ev_off_peak_charge_only_enabled = True
            elif mode is not None:
                _LOGGER.warning("%s - unknown CCS2 OffPeakTime.Mode: %s", DOMAIN, mode)
        else:
            # RE-documented alternative carrier (Kia app: Green.Reservation.
            # ChargeSchedule.Enable) — no captured payload shows it yet.
            charge_schedule_enable = get_child_value(
                state, "Green.Reservation.ChargeSchedule.Enable"
            )
            if charge_schedule_enable is not None:
                vehicle.ev_schedule_charge_enabled = bool(charge_schedule_enable)

        vehicle.washer_fluid_warning_is_on = get_child_value(
            state, "Body.Windshield.Front.WasherFluid.LevelLow"
        )
        vehicle.brake_fluid_warning_is_on = get_child_value(
            state, "Chassis.Brake.Fluid.Warning"
        )
        vehicle.fuel_level = get_child_value(state, "Drivetrain.FuelSystem.FuelLevel")
        vehicle.fuel_level_is_low = get_child_value(
            state, "Drivetrain.FuelSystem.LowFuelWarning"
        )

        # Average fuel economy (HEV/PHEV l-per-100km style, EV km/kWh style —
        # Unit enum differs per powertrain, so values and unit stay raw).
        average_fuel_economy = get_child_value(
            state, "Drivetrain.FuelSystem.AverageFuelEconomy"
        )
        if isinstance(average_fuel_economy, dict):
            vehicle.average_fuel_economy_accumulated = float_or_none(
                average_fuel_economy.get("Accumulated")
            )
            vehicle.average_fuel_economy_drive = float_or_none(
                average_fuel_economy.get("Drive")
            )
            vehicle.average_fuel_economy_after_refuel = float_or_none(
                average_fuel_economy.get("AfterRefuel")
            )
            vehicle.average_fuel_economy_unit = int_or_none(
                average_fuel_economy.get("Unit")
            )

        vehicle.air_control_is_on = get_child_value(
            state, "Cabin.HVAC.Row1.Driver.Blower.SpeedLevel"
        )
        vehicle.air_cleaning_is_on = bool_or_none(
            get_child_value(state, "Cabin.HVAC.Vent.AirCleaning.Indicator")
        )
        vehicle.smart_key_battery_warning_is_on = bool_or_none(
            get_child_value(state, "Electronics.FOB.LowBattery")
        )

        side_mirror_heat = get_child_value(state, "Cabin.SideMirror.Heating.State")
        if side_mirror_heat is not None:
            vehicle.side_mirror_heater_is_on = bool(side_mirror_heat)

        # Battery pack voltage / chiller RPM — flat CCS2-schema paths (live
        # PV5 payload + Sportage PHEV CCS2 dump; the nested "BatteryPack.
        # Voltage"/"Chiller.RPM" variants appear in no captured payload).
        bat_pack_voltage = get_child_value(
            state, "Green.BatteryManagement.BatteryPackVoltage"
        )
        if bat_pack_voltage is not None:
            vehicle.ev_battery_pack_voltage = int(bat_pack_voltage)

        chiller_rpm = get_child_value(state, "Green.BatteryManagement.ChillerRPM")
        if chiller_rpm is not None:
            vehicle.ev_battery_chiller_rpm = int(chiller_rpm)

        bat_temp_min = get_child_value(state, "Green.BatteryManagement.Temperature.Min")
        bat_temp_max = get_child_value(state, "Green.BatteryManagement.Temperature.Max")
        if isinstance(bat_temp_min, dict):
            bat_temp_min = bat_temp_min.get("Raw")
        if isinstance(bat_temp_max, dict):
            bat_temp_max = bat_temp_max.get("Raw")
        if bat_temp_min is not None:
            vehicle.ev_battery_temperature_min = (
                int(bat_temp_min),
                TEMPERATURE_UNITS[0],
            )
        if bat_temp_max is not None:
            vehicle.ev_battery_temperature_max = (
                int(bat_temp_max),
                TEMPERATURE_UNITS[0],
            )

        # Cooling-water temperature — flat CCS2-schema path (live PV5
        # payload + Sportage PHEV CCS2 dump; "Temperature.Water" appears in
        # no captured payload). Unit from TEMPERATURE_UNITS[0], not a bare
        # "C" literal.
        bat_water_temp = get_child_value(
            state, "Green.BatteryManagement.Temperature.CoolingWaterInlet"
        )
        if bat_water_temp is not None:
            vehicle.ev_battery_water_temperature = (
                int(bat_water_temp),
                TEMPERATURE_UNITS[0],
            )

        battery_heating_state = get_child_value(
            state, "Green.BatteryManagement.HeatingState"
        )
        if battery_heating_state is not None:
            vehicle.ev_battery_heating_state = bool(battery_heating_state)

        # Instantaneous power draws — flat CCS2-schema paths (live PV5
        # payload + Sportage PHEV CCS2 dump; "EnergyConsumption.*.Value"
        # appears in no captured payload).
        ev_power_ac = get_child_value(
            state, "Green.PowerConsumption.Moment.ClimateAirConditioning"
        )
        if ev_power_ac is not None:
            vehicle.ev_power_consumption_air_conditioning = float(ev_power_ac)
        ev_power_cooling = get_child_value(
            state, "Green.PowerConsumption.Moment.BatteryCooling"
        )
        if ev_power_cooling is not None:
            vehicle.ev_power_consumption_battery_cooling = float(ev_power_cooling)
        ev_power_heater = get_child_value(
            state, "Green.PowerConsumption.Moment.BatteryHeater"
        )
        if ev_power_heater is not None:
            vehicle.ev_power_consumption_battery_heater = float(ev_power_heater)

        winter_mode = get_child_value(
            state, "Green.BatteryManagement.WinterModeOperation"
        )

        # EV battery preconditioning toggle — Status enum mapping matches the
        # official app (kia_uvo #1823): 0 / 2 / 6 = off, 3 / 4 = on. Status is
        # a configuration setting; WinterModeOperation is not a user-facing
        # "Winter Mode" toggle on EVs there, so leave ev_battery_winter_mode
        # unset when Status is present. HEV-style payloads (no Status) keep
        # the WinterModeOperation behaviour as the fallback (no regression).
        battery_precondition_status = get_child_value(
            state, "Green.BatteryManagement.BatteryPreCondition.Status"
        )
        if battery_precondition_status is not None:
            vehicle.ev_battery_precondition_enabled = battery_precondition_status in (
                3,
                4,
            )
        elif winter_mode is not None:
            vehicle.ev_battery_precondition_enabled = bool(winter_mode)
            vehicle.ev_battery_winter_mode = bool(winter_mode)

        v2l_mode = get_child_value(state, "Green.Electric.SmartGrid.VehicleToLoad.mode")
        if v2l_mode is not None:
            vehicle.ev_v2l_status = bool(v2l_mode)
        v2x_mode = get_child_value(state, "Green.Electric.SmartGrid.VehicleToGrid.mode")
        if v2x_mode is not None:
            vehicle.ev_v2x_status = bool(v2x_mode)

        total_consumed = get_child_value(
            state, "Green.Electric.SmartGrid.TotalPowerConsumption"
        )
        if total_consumed is not None:
            vehicle.total_power_consumed = float(total_consumed)
        total_regen = get_child_value(
            state, "Green.Electric.SmartGrid.TotalPowerRegeneration"
        )
        if total_regen is not None:
            vehicle.total_power_regenerated = float(total_regen)

        if vehicle._ev_estimated_current_charge_duration is not None:
            if vehicle._ev_estimated_current_charge_duration == 0:
                vehicle.ev_battery_is_charging = False
            elif vehicle._ev_estimated_current_charge_duration > 0:
                vehicle.ev_battery_is_charging = True

        if get_child_value(state, "Location.GeoCoord.Latitude"):
            location_last_updated_at = dt.datetime(
                2000, 1, 1, tzinfo=self.data_timezone
            )
            timestamp = get_child_value(state, "Location.TimeStamp")
            if timestamp is not None:
                location_last_updated_at = dt.datetime(
                    year=int(get_child_value(timestamp, "Year")),
                    month=int(get_child_value(timestamp, "Mon")),
                    day=int(get_child_value(timestamp, "Day")),
                    hour=int(get_child_value(timestamp, "Hour")),
                    minute=int(get_child_value(timestamp, "Min")),
                    second=int(get_child_value(timestamp, "Sec")),
                    tzinfo=self.data_timezone,
                )
            vehicle.location = (
                get_child_value(state, "Location.GeoCoord.Latitude"),
                get_child_value(state, "Location.GeoCoord.Longitude"),
                location_last_updated_at,
            )
            vehicle._location_last_set_time = location_last_updated_at

        # R1 field gaps: drive_mode, oil_level_warning_is_on,
        # battery_auxiliary_fail_warning_is_on (mirror ApiImplType1).
        vehicle.drive_mode = get_child_value(state, "Chassis.DrivingMode.State")
        vehicle.oil_level_warning_is_on = bool_or_none(
            get_child_value(state, "Chassis.Engine.OilLevel.Status")
        )
        vehicle.battery_auxiliary_fail_warning_is_on = bool_or_none(
            get_child_value(state, "Chassis.Battery.Auxiliary.State")
        )

        vehicle.data = state

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

    # ------------------------------------------------------------------
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
            token, vehicle, "door-power-off", {"command": "set"}
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
            # tempUnit is a string on the wire ("C"/"F"); the 0/1 int form is
            # rejected with 400-002 (live-proven on a PV5, 2026-09-19).
            body["tempUnit"] = "F" if options.temp_unit == 1 else "C"
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
        if options.set_temp is not None:
            # Without tempUnit/hvacTempType the backend ignores hvacTemp and
            # applies the car's stored set point instead (live-proven on a
            # PV5; the vehicle reports Server.Option.HvacTempType = 1).
            body.setdefault("tempUnit", "C")
            body.setdefault("hvacTempType", 1)
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
                # tempUnit is a string on the wire ("C"/"F"); the 0/1 int
                # form is rejected with 400-002 (live-proven on a PV5,
                # 2026-09-19).
                body["tempUnit"] = "F" if options.temp_unit == 1 else "C"
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
            if options.set_temp is not None:
                # Without tempUnit/hvacTempType the backend ignores hvacTemp
                # and applies the car's stored set point instead (live-proven
                # on a PV5; the vehicle reports Server.Option.HvacTempType = 1).
                body.setdefault("tempUnit", "C")
                body.setdefault("hvacTempType", 1)
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
        drv_seat_loc = options.driver_seat_location or self._get_drv_seat_loc(vehicle)
        front = (options.front_left, options.front_right)
        rl = options.back_left
        rr = options.back_right
        seats = (options.front_left, options.front_right, rl, rr)
        if (
            all(s is None for s in seats)
            and options.rear_left_curtain is None
            and options.rear_right_curtain is None
        ):
            raise UnsupportedControlError("No window state requested")
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
        front_val = options.front_left.value if options.front_left is not None else None
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
            "drvSeatLoc": drv_seat_loc,
        }
        return self._gspa_control_command(token, vehicle, "windowcurtain", body)

    def set_window_curtain(
        self, token: Token, vehicle: Vehicle, options: WindowRequestOptions
    ) -> str:
        """Set per-seat windows/curtains via the window-curtain endpoint.

        Takes the universal, position-based vocabulary (front_left is the
        physical front-left window, etc.) and maps it onto the endpoint's
        seat-based body keys using drvSeatLoc: on RHD the driver's seat is
        on the right, so front_left drives psgSeatWindow and front_right
        drives drvSeatWindow. Values: 0 = close, 1 = open, 2 = vent
        (WINDOW_STATE IntEnum).
        """
        body: dict[str, Any] = {"command": "open"}
        drv_seat_loc = options.driver_seat_location or self._get_drv_seat_loc(vehicle)
        drv_key, psg_key = (
            ("psgSeatWindow", "drvSeatWindow")
            if drv_seat_loc == SEAT_LOCATION.RIGHT
            else ("drvSeatWindow", "psgSeatWindow")
        )
        if options.front_left is not None:
            body[drv_key] = options.front_left.value
        if options.front_right is not None:
            body[psg_key] = options.front_right.value
        if options.back_left is not None:
            body["rlSeatWindow"] = options.back_left.value
        if options.back_right is not None:
            body["rrSeatWindow"] = options.back_right.value
        if options.rear_left_curtain is not None:
            body["rlSeatWindowCurtain"] = options.rear_left_curtain.value
        if options.rear_right_curtain is not None:
            body["rrSeatWindowCurtain"] = options.rear_right_curtain.value
        body["drvSeatLoc"] = drv_seat_loc
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

    def schedule_reservation_charge_na(
        self,
        token: Token,
        vehicle: Vehicle,
        days: list[int],
        start: dt.time,
        end: dt.time,
    ) -> str:
        """Schedule the NA-variant charging reservation.

        The "reservation-charge-na" wire shape is a single weekly charge
        window (active days + start time + end time) with no climate and
        no target-SOC scope; the app hardcodes ``reservChargeSet`` to
        ``false``. Because most ``ScheduleChargingClimateRequestOptions``
        fields have no wire counterpart here, the window is taken
        directly (app-faithful: the use case passes isOn/end/days/start
        and the datasource drops the on/off flag).

        ``days`` uses the Sun=0..Sat=6 encoding shared by the other
        schedule methods. Times are interpreted as wall clock in
        ``data_timezone`` and converted to the UTC "HH:MM:00Z" form the
        endpoint expects; day indices shift across the UTC midnight
        boundary accordingly (the app converts device-local times the
        same way). Bearer-authenticated (no PIN control token). Like the
        other unverified reservation endpoints it raises
        NotImplementedError on brands without live verification (D6).
        """
        if not days:
            raise ValueError("schedule_reservation_charge_na requires at least one day")

        def _to_utc(t: dt.time, day: int | None = None) -> tuple[int, str]:
            """Convert a local wall-clock time to (UTC weekday, "HH:MM:00Z")."""
            reference = dt.datetime.now(self.data_timezone)
            if day is None:
                date = reference.date()
            else:
                target_py_weekday = (day - 1) % 7  # Sun=0 encoding -> py Mon=0
                date = reference.date() + dt.timedelta(
                    days=(target_py_weekday - reference.weekday()) % 7
                )
            aware = dt.datetime.combine(date, t).replace(tzinfo=self.data_timezone)
            utc = aware.astimezone(dt.UTC)
            return (utc.weekday() + 1) % 7, utc.strftime("%H:%M:00Z")

        wire_days: list[int] = []
        start_wire = ""
        for day in sorted(set(days)):
            wire_day, start_wire = _to_utc(start, day)
            wire_days.append(wire_day)
        end_wire = _to_utc(end)[1]

        body: dict[str, Any] = {
            "reservChargeInfo": {
                "reservInfo": {"day": wire_days, "time": start_wire},
                "reservEndTime": end_wire,
                "reservChargeSet": False,
            }
        }
        return self._gspa_control_command(token, vehicle, "reservation-charge-na", body)

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

    # ------------------------------------------------------------------
    # GSPA extended reads (brand-neutral paths)
    # ------------------------------------------------------------------

    def get_location_update_status(
        self, token: Token, vehicle: Vehicle
    ) -> dict[str, Any] | None:
        """Poll fresh parked location from GSPA (location update-status).

        Live probe (2026-09-04, Hyundai EU Santa Fe): HTTP 400 400-004.
        The path matches the app (plain GET, app holds it as a 140s
        long-poll). Probed with the car in an underground garage (no GPS
        fix), so the 400 may simply mean "no location available" —
        re-probe outdoors before assuming a wire-shape mismatch.
        """
        self._validate_ccs_token(token)
        try:
            return self._gspa_get(
                token, vehicle, "location/vehicles/{carId}/update-status"
            )
        except AuthenticationError:
            raise
        except Exception:
            _LOGGER.debug(f"{DOMAIN} - GSPA location update-status failed")
            return None

    def get_location_stored_status(
        self, token: Token, vehicle: Vehicle
    ) -> dict[str, Any] | None:
        """Get cached location + vehicle status from GSPA (read-only)."""
        self._validate_ccs_token(token)
        try:
            return self._gspa_get(
                token, vehicle, "location/vehicles/{carId}/stored-status"
            )
        except AuthenticationError:
            raise
        except Exception:
            _LOGGER.debug(f"{DOMAIN} - GSPA location stored-status failed")
            return None

    def get_location_routes(
        self, token: Token, vehicle: Vehicle
    ) -> dict[str, Any] | None:
        """Get POI/route history from GSPA."""
        self._validate_ccs_token(token)
        try:
            return self._gspa_get(token, vehicle, "location/vehicles/{carId}/routes")
        except AuthenticationError:
            raise
        except Exception:
            _LOGGER.debug(f"{DOMAIN} - GSPA location routes failed")
            return None

    def get_valet_status(self, token: Token, vehicle: Vehicle) -> dict[str, Any] | None:
        """Get valet mode status from GSPA."""
        self._validate_ccs_token(token)
        try:
            return self._gspa_get(token, vehicle, "valet/vehicles/{carId}/status")
        except AuthenticationError:
            raise
        except Exception:
            _LOGGER.debug(f"{DOMAIN} - GSPA valet status failed")
            return None

    def get_valet_history(
        self, token: Token, vehicle: Vehicle
    ) -> dict[str, Any] | None:
        """Get valet mode history from GSPA."""
        self._validate_ccs_token(token)
        try:
            return self._gspa_get(token, vehicle, "valet/vehicles/{carId}/history")
        except AuthenticationError:
            raise
        except Exception:
            _LOGGER.debug(f"{DOMAIN} - GSPA valet history failed")
            return None

    def get_safety_data(self, token: Token, vehicle: Vehicle) -> dict[str, Any] | None:
        """Get vehicle safety alert settings from GSPA.

        Uses the app's alert-setting path (D6 naming).
        """
        self._validate_ccs_token(token)
        try:
            return self._gspa_get(
                token, vehicle, "safety/vehicles/{carId}/alert-setting"
            )
        except AuthenticationError:
            raise
        except Exception:
            _LOGGER.debug(f"{DOMAIN} - GSPA safety_data failed")
            return None

    def get_stored_status_widget(
        self, token: Token, vehicle: Vehicle
    ) -> dict[str, Any] | None:
        """Get cached vehicle status in widget format from GSPA."""
        self._validate_ccs_token(token)
        try:
            return self._gspa_get(
                token, vehicle, "status/vehicles/{carId}/stored-status-widget"
            )
        except AuthenticationError:
            raise
        except Exception:
            _LOGGER.debug(f"{DOMAIN} - GSPA stored-status-widget failed")
            return None

    def get_gspa_vehicles(self, token: Token) -> list[dict[str, Any]] | None:
        """Get the enrolled vehicle list from GSPA.

        GET /gspa/v1/vehicles (not vehicle-bound, no carId substitution).
        """
        self._validate_ccs_token(token)
        headers = self._get_authenticated_headers(token, 0)
        url = self.CCSP_API_URL + "/gspa/v1/vehicles"
        try:
            response = requests.get(url, headers=headers, timeout=(5, 30))
            if response.status_code == 401:
                raise AuthenticationError("GSPA: Token expired or invalid")
            data: dict[str, Any] = response.json()
            meta: dict[str, Any] = data.get("metaInfo", {})
            if meta.get("retCode") != "S":
                _LOGGER.debug(
                    f"{DOMAIN} - GSPA vehicles list: {meta.get('resCode', '')} "
                    f"{meta.get('message', '')}"
                )
                return None
            result: list[dict[str, Any]] = data.get("data", [])
            return result
        except AuthenticationError:
            raise
        except Exception:
            _LOGGER.debug(f"{DOMAIN} - GSPA vehicles list failed")
            return None

    def get_weather(
        self, token: Token, latitude: float, longitude: float
    ) -> dict[str, Any] | None:
        """Get weather at the given coordinates from GSPA.

        Wire shape confirmed from the app's shared CCS SDK request
        construction: the endpoint requires
        ``?currentCoordinate=<lat>,<lon>`` (Java ``%f,%f`` — 6 decimal
        places) and ``attributes=currentWeather``. The earlier live
        probe (2026-09-04) returned HTTP 400 400-007 — sent with no
        query params, which alone explains the rejection.
        """
        self._validate_ccs_token(token)
        headers = self._get_authenticated_headers(token, 0)
        url = self.CCSP_API_URL + "/gspa/v1/contents/wts/weathers"
        params = {
            "currentCoordinate": f"{latitude:.6f},{longitude:.6f}",
            "attributes": "currentWeather",
        }
        try:
            response = requests.get(
                url, headers=headers, params=params, timeout=(5, 30)
            )
            if response.status_code == 401:
                raise AuthenticationError("GSPA: Token expired or invalid")
            data: dict[str, Any] = response.json()
            meta: dict[str, Any] = data.get("metaInfo", {})
            if meta.get("retCode") != "S":
                _LOGGER.debug(
                    f"{DOMAIN} - GSPA weather: {meta.get('resCode', '')} "
                    f"{meta.get('message', '')}"
                )
                return None
            result: dict[str, Any] = data.get("data", {})
            return result
        except AuthenticationError:
            raise
        except Exception:
            _LOGGER.debug(f"{DOMAIN} - GSPA weather failed")
            return None
