"""HyundaiCciApiEU.py — Hyundai EU CCI/GSPA API (login + token + GSPA stamp setup).

PR1 scope: login, CCI token exchange, CCS token exchange, device registration,
CCS user-id extraction, token refresh, and GSPA X-Stamp computation.

Not in PR1: GSPA vehicle data, control, OTA, MQTT (Tasks 7, PR2-4).
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
from .const import BRAND_GENESIS, BRAND_HYUNDAI, BRAND_KIA, BRANDS, DOMAIN
from .exceptions import APIError, AuthenticationError, ConsentRequiredError
from .Token import Token

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

    def __init__(
        self, region: int, brand: int, language: str, staging: bool = False
    ) -> None:
        language = language.lower()
        if len(language) > 2:
            language = language[0:2]
        if language not in SUPPORTED_LANGUAGES_LIST:
            _LOGGER.warning(f"Unsupported language: {language}, fallback to en")
            language = "en"

        self.region: int = region
        self.LANGUAGE: str = language
        self.brand: int = brand
        self.staging: bool = staging

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
        """Headers for the CCI API (cci-api-eu.hyundai.com).

        - Authentication: raw nonCcsToken (no Bearer prefix)
        - authorization: "Bearer " + CCI accessToken
        - exchangeable-token / non-ccs-token (kebab-case)
        """
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
    # GSPA X-Stamp computation
    # ------------------------------------------------------------------

    def _get_stamp(self, token: Token) -> str | None:
        """Compute GSPA X-Stamp header value.

        X-Stamp = base64(encrypt_cfb(iv_for_region, payload))
        payload = "{tsid}:{epoch_seconds}:{user_id}"

        Region 1 (EU) uses IVS[1]. user_id is the uid claim from the
        CCS token JWT (token.ccs_user_id).

        Returns None if the gspa cipher is not available.
        """
        try:
            from .gspa import compute_x_stamp, create_tsid

            device_id = (token.device_id or "").replace("-", "")
            tsid = create_tsid(device_id)
            epoch_seconds = int(dt.datetime.now(dt.UTC).timestamp())
            user_id = token.ccs_user_id or ""
            stamp = compute_x_stamp(
                region=self.region,
                is_production=not self.staging,
                tsid=tsid,
                epoch_seconds=epoch_seconds,
                user_id=user_id,
            )
            return stamp
        except NotImplementedError:
            # Staging or unsupported region — fail loud, don't silently
            # produce a wrong X-Stamp.
            raise
        except Exception as e:
            _LOGGER.debug(f"{DOMAIN} - X-Stamp computation error: {e}")
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
            m = re.search(r"t=([^;]+)", set_cookie)
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
