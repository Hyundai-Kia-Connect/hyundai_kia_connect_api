"""HyundaiCciApiEU.py — Hyundai EU CCI/GSPA API.

PR1 scope: login, CCI token exchange, CCS token exchange, device registration,
CCS user-id extraction, token refresh, GSPA X-Stamp computation, GSPA vehicle
data (stored-status, driving info/history, breakdowns), and the CCS2 status
parser. Control, OTA, and MQTT are handled in later PRs.
"""

# pylint:disable=missing-class-docstring,missing-function-docstring,invalid-name,logging-fstring-interpolation,broad-except,too-many-lines

import base64
import datetime as dt
import hashlib
import json
import logging
import re
import uuid
from urllib.parse import parse_qs, urlparse

import requests
from Crypto.Cipher import PKCS1_v1_5
from Crypto.PublicKey import RSA

from .ApiImpl import ApiImpl, ApiImplSession
from .const import (
    BRAND_GENESIS,
    BRAND_HYUNDAI,
    BRAND_KIA,
    BRANDS,
    DISTANCE_UNITS,
    DOMAIN,
    ENGINE_TYPES,
    PRESSURE_SCALES,
    SEAT_STATUS,
    TEMPERATURE_UNITS,
    PressureUnit,
)
from .exceptions import APIError, AuthenticationError, ConsentRequiredError
from .Token import Token
from .utils import (
    bool_or_none,
    get_child_value,
    normalize_battery_soc,
    parse_datetime,
    pressure_or_none,
)
from .Vehicle import DailyDrivingStats, Vehicle

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


class HyundaiCciApiEU(ApiImpl):
    """Hyundai EU CCI/GSPA API.

    Uses the CCI login flow (OneApp client_id 4f4953b5) confirmed on
    production endpoints. GSPA X-Stamp computation is available via
    the pure-Python gspa cipher for future GSPA REST calls.
    """

    data_timezone = dt.UTC
    supports_valet_mode = True

    def __init__(self, region: int, brand: int, language: str) -> None:
        language = language.lower()
        if len(language) > 2:
            language = language[0:2]
        if language not in SUPPORTED_LANGUAGES_LIST:
            _LOGGER.warning(f"Unsupported language: {language}, fallback to en")
            language = "en"

        self.region: int = region
        self.LANGUAGE: str = language
        self.brand: int = brand

        if BRANDS[self.brand] == BRAND_HYUNDAI:
            # Confirmed production endpoints.
            self.ONEAPP_CLIENT_ID: str = "4f4953b5-02e1-4dbc-8599-87e983ee1be5"
            self.ONEAPP_REDIRECT_URI: str = "https://oneapp.hyundai.com/redirect"
            self.CCI_API_URL: str = "https://cci-api-eu.hyundai.com"
            self.LOGIN_FORM_HOST: str = "https://idpconnect-eu.hyundai.com"
            self._cci_package_id: str = "com.hyundai.oneapp.eu"
            self._cci_client_name: str = "hyundai"
        elif BRANDS[self.brand] == BRAND_KIA:
            raise NotImplementedError(
                "Kia CCI EU not yet implemented — use KiaUvoApiEU for Kia EU."
            )
        elif BRANDS[self.brand] == BRAND_GENESIS:
            raise NotImplementedError(
                "Genesis CCI EU not yet implemented — use KiaUvoApiEU for Genesis EU."
            )
        else:
            raise APIError(f"Unknown brand {BRANDS[self.brand]} for CCI EU API")

        self.CCI_DOMAIN_API_URL: str = self.CCI_API_URL + "/domain/api/"
        self.CCSP_API_URL: str = "https://gspa-ccs-eu.hyundai.com"
        self.CCSP_SERVICE_ID: str = "6d477c38-3ca4-4cf3-9557-2a1929a94654"
        self._cci_client_version: str = "1.3.3"
        self._cci_client_os_version: str = "18.7"
        self._cci_notification_provider: str = "APNS"

        self.session = ApiImplSession()

    # ------------------------------------------------------------------
    # Login
    # ------------------------------------------------------------------

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

        info = self._login_with_password(username, password, device_id)

        token = Token(
            username=username,
            password=password,
            access_token=info["access_token"],
            refresh_token=info["refresh_token"],
            device_id=device_id,
            valid_until=info["valid_until"],
            pin=pin,
            cci_access_token=info.get("cci_access_token"),
            exchangeable_token=info.get("exchangeable_token"),
            exchangeable_refresh_token=info.get("exchangeable_refresh_token"),
            non_ccs_token=info.get("non_ccs_token"),
            non_ccs_refresh_token=info.get("non_ccs_refresh_token"),
            id_token=info.get("id_token"),
        )

        # Register device on CCI (non-critical — best effort).
        self._register_device(token)

        # Extract CCS user-id for GSPA X-Stamp (best effort).
        self._fetch_ccs_user_id(token)

        return token

    def _login_with_password(
        self, username: str, password: str, device_id: str
    ) -> dict:
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
                "This may indicate a Hyundai API change."
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
    ) -> dict:
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
                f"{resp.text[:200]}. This may indicate a Hyundai API change."
            )
        return resp.json()

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
    ) -> dict:
        """Headers for the CCI API."""
        headers = {
            "client-id": self._cci_package_id,
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
                f"{resp.text[:200]}. This may indicate a Hyundai API change."
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

    def _fetch_ccs_user_id(self, token: Token) -> None:
        """Populate token.ccs_user_id for GSPA X-Stamp computation.

        The X-Stamp payload requires the 'uid' claim from the ccs_token JWT.
        Fallback chain:
        1. Extract 'uid' from ccs_token JWT (primary)
        2. Extract 'sub' from id_token JWT (fallback)
        """
        if token.ccs_user_id:
            return

        # Primary: uid claim from CCS token JWT
        # The CCS token is stored as access_token (with "Bearer " prefix)
        ccs_token = (token.access_token or "").removeprefix("Bearer ")
        if ccs_token:
            uid = self._extract_jwt_claim(ccs_token, "uid")
            if uid:
                token.ccs_user_id = uid
                _LOGGER.debug(f"{DOMAIN} - CCS user ID from ccs_token.uid: {uid}")
                return

        # Fallback: sub from id_token
        if token.id_token:
            sub = self._extract_jwt_claim(token.id_token, "sub")
            if sub:
                token.ccs_user_id = sub
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
            return payload.get(claim)
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Token refresh
    # ------------------------------------------------------------------

    def refresh_access_token(self, token: Token) -> Token:
        """Refresh access token using the stored CCI token set.

        CCI flow: POST v1/auth/token-refresh with the full token set,
        then re-exchange the CCS token. Falls back to full login if
        the refresh token is missing or the exchange fails.
        """
        if getattr(token, "cci_access_token", None) or getattr(
            token, "non_ccs_token", None
        ):
            try:
                return self._refresh_cci_token(token)
            except Exception:
                _LOGGER.warning("CCI token refresh failed, falling back to full login")
                return self.login(token.username, token.password, token.pin)

        # No CCI tokens — fall back to full login
        return self.login(token.username, token.password, token.pin)

    def _refresh_cci_token(self, token: Token) -> Token:
        """Refresh the CCI token set and re-exchange the CCS token.

        POST cci-api-eu/domain/api/v1/auth/token-refresh (v1, not v2)
        with the full CCI token set, then re-exchange the CCS token.
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
            f"{self.CCI_DOMAIN_API_URL}v1/auth/token-refresh",
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
            ccs_user_id=token.ccs_user_id,
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
            return response.status_code == 200
        except Exception:
            _LOGGER.debug(f"{DOMAIN} - CCS token freshness check failed")
            return False

    # ------------------------------------------------------------------
    # GSPA X-Stamp computation
    # ------------------------------------------------------------------

    def _get_stamp(self, token: Token) -> tuple[str | None, str | None]:
        """Compute GSPA X-Stamp + tsid for GSPA endpoint authentication.

        Returns (stamp, tsid) — both must be sent as X-Stamp + X-Request-Id
        headers. The server validates the stamp against the tsid.

        Returns (None, None) if computation fails.
        """
        try:
            from .gspa import compute_x_stamp, create_tsid

            device_id = (token.device_id or "").replace("-", "")
            tsid = create_tsid(device_id)
            epoch_seconds = int(dt.datetime.now(dt.UTC).timestamp())
            user_id = token.ccs_user_id or ""
            stamp = compute_x_stamp(
                region=self.region,
                tsid=tsid,
                epoch_seconds=epoch_seconds,
                user_id=user_id,
            )
            return stamp, tsid
        except NotImplementedError:
            raise
        except Exception as e:
            _LOGGER.debug(f"{DOMAIN} - X-Stamp computation error: {e}")
            return None, None

    # ------------------------------------------------------------------
    # GSPA authenticated headers
    # ------------------------------------------------------------------

    def _get_authenticated_headers(self, token: Token, ccs2_support: int = 0) -> dict:
        """Headers for GSPA REST endpoints (gspa-ccs-eu.hyundai.com)."""
        ccs_token = (token.access_token or "").removeprefix("Bearer ")
        headers = {
            "Authorization": f"Bearer {ccs_token}",
            "ccsp-service-id": self.CCSP_SERVICE_ID,
            "ccsp-application-id": self.CCSP_SERVICE_ID,
            "ccsp-device-id": token.device_id or "",
            "X-Device-Id": token.device_id or "",
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
        if stamp and tsid:
            headers["X-Stamp"] = stamp
            headers["X-Request-Id"] = tsid
        return headers

    def _ensure_ccs_token(self, token: Token) -> None:
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

    # ------------------------------------------------------------------
    # GSPA GET helper
    # ------------------------------------------------------------------

    def _gspa_get(
        self,
        token: Token,
        vehicle: Vehicle,
        endpoint: str,
        params: dict | None = None,
    ) -> dict | None:
        """GET from a GSPA endpoint (X-Stamp gated).

        GSPA endpoints use the CCSP host + /gspa/v1/ prefix.
        Response envelope: {"data": {...}, "metaInfo": {"retCode": "S", "resCode": "200-000"}}
        Returns the data (domain payload) dict, or None on business errors.
        """
        self._ensure_ccs_token(token)
        car_id = vehicle.id
        url = self.CCSP_API_URL + f"/gspa/v1/{endpoint.format(carId=car_id)}"
        headers = self._get_authenticated_headers(
            token, vehicle.ccu_ccs2_protocol_support or 0
        )

        response = requests.get(url, headers=headers, params=params, timeout=(5, 30))
        if response.status_code == 401:
            raise AuthenticationError("GSPA: Token expired or invalid")
        if response.status_code >= 400:
            raise APIError(f"GSPA error: HTTP {response.status_code}")
        data = response.json()
        meta = data.get("metaInfo", {})
        ret_code = meta.get("retCode")
        res_code = meta.get("resCode", "")

        if response.status_code == 401:
            raise AuthenticationError("CCSP: Token expired or invalid")
        if response.status_code == 403:
            raise APIError(f"GSPA auth error: {res_code} {meta.get('message', '')}")

        if ret_code != "S":
            _LOGGER.debug(
                f"{DOMAIN} - GSPA GET {endpoint}: {res_code} {meta.get('message', '')}"
            )
            return None

        return data.get("data")

    # ------------------------------------------------------------------
    # GSPA stored-status
    # ------------------------------------------------------------------

    def get_stored_status(self, token: Token, vehicle: Vehicle) -> dict | None:
        """Get cached vehicle status from GSPA stored-status endpoint.

        Returns the data dict from the GSPA response, or None on failure.
        The response contains vehicle state in CCS2 nested format
        (Green.BatteryManagement.*, Cabin.HVAC.*, etc.).
        """
        self._ensure_ccs_token(token)
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
    # Vehicle list
    # ------------------------------------------------------------------

    def get_vehicles(self, token: Token) -> list[Vehicle]:
        """Get the list of vehicles from CCI (cci-api-eu, no CCAPI fallback)."""
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

    def _parse_vehicles_from_cci(self, data: dict) -> list[Vehicle]:
        vehicles: list[Vehicle] = []
        vehicle_list = (
            data
            if isinstance(data, list)
            else data.get("contents", data.get("vehicles", []))
        )
        if isinstance(vehicle_list, dict):
            vehicle_list = [vehicle_list]

        for entry in vehicle_list:
            vehicle = Vehicle()
            ccsp = entry.get("ccspVehicle", {})
            vehicle.id = (
                entry.get("ccspCarId")
                or (ccsp.get("carId") if ccsp else None)
                or entry.get("vehicleId", "")
            )
            vehicle.VIN = entry.get("vin", "")
            vehicle.name = entry.get(
                "vehicleNameView",
                entry.get("nickname", entry.get("vehicleName", "")),
            )
            vehicle.model = entry.get("vehicleModelName", entry.get("modelName", ""))
            vehicle.ccu_ccs2_protocol_support = entry.get(
                "ccs2ProtocolSupport", entry.get("ccu_ccs2_protocol_support", 0)
            )
            if not vehicle.ccu_ccs2_protocol_support:
                is_ccs = entry.get("isCcs", False)
                is_ccs_open = entry.get("isCcsOpen", False)
                if is_ccs and is_ccs_open:
                    vehicle.ccu_ccs2_protocol_support = 2

            car_type = (ccsp.get("carType") if ccsp else "") or ""
            is_ev = entry.get("isEv", False)
            fuel_type = entry.get("fuelType", entry.get("engineFuelCode", ""))
            if is_ev or fuel_type == "EV" or car_type in ("EV", "ELEC"):
                vehicle.engine_type = ENGINE_TYPES.EV
            elif fuel_type in ("PHEV", "HEV+PHEV") or car_type in ("PHEV",):
                vehicle.engine_type = ENGINE_TYPES.PHEV
            elif fuel_type == "HEV" or car_type in ("HEV", "HV"):
                vehicle.engine_type = ENGINE_TYPES.HEV
            else:
                vehicle.engine_type = ENGINE_TYPES.ICE

            vehicle.vehicle_default_image_url = entry.get(
                "vehicleDefaultImageUrl", entry.get("vehicleImageUrl")
            )
            vehicle.web_manual_url = entry.get("webManualURL")
            vehicle.color_code = entry.get("colorCode")
            vehicle.banner_image_url = entry.get(
                "bannerImageUrl", entry.get("vehicleOuterImageUrl")
            )
            vehicle.model_config_data_url = entry.get("modelConfigDataUrl")
            vehicle._cci_vehicle_data = entry

            vehicles.append(vehicle)

        return vehicles

    # ------------------------------------------------------------------
    # Force refresh
    # ------------------------------------------------------------------

    def force_refresh_vehicle_state(self, token: Token, vehicle: Vehicle) -> None:
        """Wake the vehicle and re-read GSPA stored-status.

        Prewakeup is best-effort (car may be offline). The status read
        returns the last cached state regardless.
        """
        self._ensure_ccs_token(token)
        try:
            self.prewakeup(token, vehicle)
        except Exception:
            _LOGGER.debug(f"{DOMAIN} - prewakeup failed (car may be offline)")
        self.update_vehicle_with_cached_state(token, vehicle)

    def prewakeup(self, token: Token, vehicle: Vehicle) -> dict | None:
        """Send a prewakeup command to bring the vehicle online."""
        car_id = vehicle.id
        url = self.CCSP_API_URL + f"/gspa/v1/remote/vehicles/{car_id}/prewakeup"
        self._ensure_ccs_token(token)
        headers = self._get_authenticated_headers(
            token, vehicle.ccu_ccs2_protocol_support or 0
        )
        try:
            response = requests.post(url, headers=headers, timeout=(5, 60))
            if response.status_code == 401:
                raise AuthenticationError("GSPA: Token expired or invalid")
            if response.status_code >= 400:
                raise APIError(
                    f"GSPA control error: HTTP {response.status_code} - "
                    f"{response.text[:200]}"
                )
            data = response.json()
            rc = data.get("rc")
            if rc and rc != "0000":
                raise APIError(f"GSPA error: rc={rc}, msg={data.get('msg', '')}")
            return data.get("rs", data)
        except AuthenticationError:
            raise
        except Exception:
            _LOGGER.debug(f"{DOMAIN} - GSPA prewakeup failed")
            return None

    # ------------------------------------------------------------------
    # Driving info + history (GSPA, read-only)
    # ------------------------------------------------------------------

    def _get_driving_info(self, token: Token, vehicle: Vehicle) -> dict | None:
        """Fetch driving info from GSPA driving-info endpoint."""
        self._ensure_ccs_token(token)
        try:
            return self._gspa_get(token, vehicle, "driving-info/vehicles/{carId}")
        except Exception:
            _LOGGER.debug(f"{DOMAIN} - GSPA driving-info failed")
            return None

    def _update_vehicle_drive_info(self, vehicle: Vehicle, state: dict) -> None:
        if isinstance(state, dict):
            driving_info = state.get("drivingInfo", state)
            if driving_info is None:
                return
            if isinstance(driving_info, list) and len(driving_info) > 0:
                driving_info = driving_info[0]
            vehicle.total_driving_range = (
                driving_info.get("totalDistance"),
                DISTANCE_UNITS.get(1, "km"),
            )
            total_consumed = driving_info.get("totalPwrCsp")
            if total_consumed is not None:
                vehicle.total_power_consumed = float(total_consumed)
            total_regen = driving_info.get("regenPwr")
            if total_regen is not None:
                vehicle.total_power_regenerated = float(total_regen)

    def _get_driving_history(self, token: Token, vehicle: Vehicle) -> dict | None:
        """Fetch 30-day driving history from GSPA driving-history endpoint."""
        self._ensure_ccs_token(token)
        try:
            return self._gspa_get(token, vehicle, "driving-history/vehicles/{carId}")
        except Exception:
            _LOGGER.debug(f"{DOMAIN} - GSPA driving-history failed")
            return None

    def _update_vehicle_driving_history(self, vehicle: Vehicle, state: dict) -> None:
        """Parse 30-day driving history into power_consumption_30d and daily_stats."""
        driving_info_list = state.get("drivingInfo", [])
        if not driving_info_list:
            return

        for item in driving_info_list:
            if not isinstance(item, dict):
                continue
            if item.get("drivingPeriod") != 0:
                continue
            total_pwr = item.get("totalPwrCsp")
            odo = next(
                (v for k, v in item.items() if k.lower() == "calculativeodo"),
                0,
            )
            if total_pwr and odo and odo > 0:
                vehicle.power_consumption_30d = round(total_pwr / odo)
                break

        detail_list = state.get("drivingInfoDetail", [])
        if detail_list:
            daily_stats = []
            for day in detail_list:
                if not isinstance(day, dict):
                    continue
                try:
                    processed = DailyDrivingStats(
                        date=dt.datetime.strptime(day["drivingDate"], "%Y%m%d").replace(
                            tzinfo=self.data_timezone
                        ),
                        total_consumed=get_child_value(day, "totalPwrCsp"),
                        engine_consumption=get_child_value(day, "motorPwrCsp"),
                        climate_consumption=get_child_value(day, "climatePwrCsp"),
                        onboard_electronics_consumption=get_child_value(
                            day, "eDPwrCsp"
                        ),
                        battery_care_consumption=get_child_value(
                            day, "batteryMgPwrCsp"
                        ),
                        regenerated_energy=get_child_value(day, "regenPwr"),
                        distance=get_child_value(day, "calculativeOdo"),
                        distance_unit=vehicle.odometer_unit,
                    )
                    daily_stats.append(processed)
                except (KeyError, ValueError):
                    continue
            if daily_stats:
                vehicle.daily_stats = daily_stats

    # ------------------------------------------------------------------
    # DTC breakdowns (GSPA, read-only)
    # ------------------------------------------------------------------

    def get_breakdowns(self, token: Token, vehicle: Vehicle) -> dict | None:
        """Get vehicle diagnostic trouble codes (DTCs) from GSPA."""
        self._ensure_ccs_token(token)
        try:
            return self._gspa_get(
                token, vehicle, "diagnostics/vehicles/{carId}/breakdowns"
            )
        except Exception:
            _LOGGER.debug(f"{DOMAIN} - GSPA breakdowns failed")
            return None

    def _parse_breakdowns(self, vehicle: Vehicle, data: dict) -> None:
        """Parse DTC data from GSPA breakdown response.

        Response structure:
          {"breakdown": [{"ecuName": "...", "ecuIdx": 0, "dtcList": [...]}]}
        """
        breakdown = data.get("breakdown", [])
        if not breakdown:
            return
        vehicle.dtc_count = len(breakdown)
        descriptions = {}
        for item in breakdown:
            if not isinstance(item, dict):
                continue
            ecu_name = item.get("ecuName", item.get("ecuIdx", "unknown"))
            dtc_list = item.get("dtcList", [])
            if dtc_list:
                descriptions[str(ecu_name)] = dtc_list
        if descriptions:
            vehicle.dtc_descriptions = descriptions

    # ------------------------------------------------------------------
    # CCS2 vehicle property mapping
    # ------------------------------------------------------------------

    def _update_vehicle_properties_ccs2(self, vehicle: Vehicle, state: dict) -> None:
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

        # Drivetrain / ignition state
        vehicle.transmission_condition = get_child_value(
            state, "Drivetrain.Transmission.ParkingPosition"
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
        vehicle.ev_battery_soh_percentage = get_child_value(
            state, "Green.BatteryManagement.SoH.Ratio"
        )
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
        ev_charging_power = get_child_value(
            state, "Green.Electric.SmartGrid.RealTimePower"
        )
        if ev_charging_power is not None:
            vehicle.ev_charging_power = float(ev_charging_power)
        vehicle.ev_v2l_discharge_limit = get_child_value(
            state, "Green.Electric.SmartGrid.VehicleToLoad.DischargeLimitation.SoC"
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

        off_peak_start = get_child_value(
            state, "Green.Reservation.OffPeakPower.StartTime"
        )
        if off_peak_start is not None:
            vehicle.ev_off_peak_start_time = off_peak_start
        off_peak_end = get_child_value(state, "Green.Reservation.OffPeakPower.EndTime")
        if off_peak_end is not None:
            vehicle.ev_off_peak_end_time = off_peak_end
        off_peak_only = get_child_value(
            state, "Green.Reservation.OffPeakPower.OffPeakOnly"
        )
        if off_peak_only is not None:
            vehicle.ev_off_peak_charge_only_enabled = bool(off_peak_only)
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
        vehicle.air_control_is_on = get_child_value(
            state, "Cabin.HVAC.Row1.Driver.Blower.SpeedLevel"
        )
        vehicle.smart_key_battery_warning_is_on = bool(
            get_child_value(state, "Electronics.FOB.LowBattery")
        )

        side_mirror_heat = get_child_value(state, "Cabin.SideMirror.Heating.State")
        if side_mirror_heat is not None:
            vehicle.side_mirror_heater_is_on = bool(side_mirror_heat)

        bat_pack_voltage = get_child_value(
            state, "Green.BatteryManagement.BatteryPack.Voltage"
        )
        if bat_pack_voltage is not None:
            vehicle.ev_battery_pack_voltage = int(bat_pack_voltage)

        chiller_rpm = get_child_value(state, "Green.BatteryManagement.Chiller.RPM")
        if chiller_rpm is not None:
            vehicle.ev_battery_chiller_rpm = int(chiller_rpm)

        bat_temp_min = get_child_value(state, "Green.BatteryManagement.Temperature.Min")
        bat_temp_max = get_child_value(state, "Green.BatteryManagement.Temperature.Max")
        if isinstance(bat_temp_min, dict):
            bat_temp_min = bat_temp_min.get("Raw")
        if isinstance(bat_temp_max, dict):
            bat_temp_max = bat_temp_max.get("Raw")
        if bat_temp_min is not None:
            vehicle.ev_battery_temperature_min = (int(bat_temp_min), "C")
        if bat_temp_max is not None:
            vehicle.ev_battery_temperature_max = (int(bat_temp_max), "C")

        bat_water_temp = get_child_value(
            state, "Green.BatteryManagement.Temperature.Water"
        )
        if bat_water_temp is not None:
            vehicle.ev_battery_water_temperature = (int(bat_water_temp), "C")

        battery_heating_state = get_child_value(
            state, "Green.BatteryManagement.HeatingState"
        )
        if battery_heating_state is not None:
            vehicle.ev_battery_heating_state = bool(battery_heating_state)

        ev_power_ac = get_child_value(
            state, "Green.EnergyConsumption.AirConditioning.Value"
        )
        if ev_power_ac is not None:
            vehicle.ev_power_consumption_air_conditioning = float(ev_power_ac)
        ev_power_cooling = get_child_value(
            state, "Green.EnergyConsumption.BatteryCooling.Value"
        )
        if ev_power_cooling is not None:
            vehicle.ev_power_consumption_battery_cooling = float(ev_power_cooling)
        ev_power_heater = get_child_value(
            state, "Green.EnergyConsumption.BatteryHeater.Value"
        )
        if ev_power_heater is not None:
            vehicle.ev_power_consumption_battery_heater = float(ev_power_heater)

        winter_mode = get_child_value(
            state, "Green.BatteryManagement.WinterModeOperation"
        )
        if winter_mode is not None:
            vehicle.ev_battery_winter_mode = bool(winter_mode)

        battery_precondition = get_child_value(
            state, "Green.BatteryManagement.BatteryPreCondition"
        )
        if battery_precondition is not None:
            vehicle.ev_battery_precondition_enabled = bool(battery_precondition)

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
    # Update vehicle with cached state
    # ------------------------------------------------------------------

    def update_vehicle_with_cached_state(self, token: Token, vehicle: Vehicle) -> None:
        """Fetch GSPA stored-status and update vehicle properties.

        GSPA stored-status is the primary status for all vehicles. The
        response shape is:
          {serviceNo, lastUpdateTime, state:{Vehicle:{Body,Cabin,Chassis,
          Drivetrain,Green,Electronics,Location,...}}}
        state.Vehicle is the ccs2 vehicleStatus — fed to
        _update_vehicle_properties_ccs2.
        """
        if not (token.access_token or token.exchangeable_token):
            raise APIError("No CCS token — cannot fetch GSPA stored-status")
        data = self.get_stored_status(token, vehicle)
        if not data:
            raise APIError("GSPA stored-status returned no data")
        state = data.get("state", {})
        if isinstance(state, dict) and "Vehicle" in state:
            state = state["Vehicle"]
        self._update_vehicle_properties_ccs2(vehicle, state)
        vehicle.data = state

        if vehicle.engine_type in (ENGINE_TYPES.EV, ENGINE_TYPES.PHEV):
            try:
                state = self._get_driving_info(token, vehicle)
                if state:
                    self._update_vehicle_drive_info(vehicle, state)
            except Exception:
                _LOGGER.debug(f"{DOMAIN} - Driving info fetch failed")
            try:
                history = self._get_driving_history(token, vehicle)
                if history and isinstance(history, dict):
                    self._update_vehicle_driving_history(vehicle, history)
            except Exception:
                _LOGGER.debug(f"{DOMAIN} - Driving history fetch failed")
