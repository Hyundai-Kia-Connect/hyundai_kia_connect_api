"""CCI auth helpers shared by the EU OneApp/CCI login flows.

KiaUvoApiEU (region 1, CCAPI vehicle endpoints) and GspaApiEU (region 9,
GSPA vehicle endpoints) log in through the same cci-api-eu host. The
header builders and token exchanges below were duplicated in both classes;
they live here so a fix to the gate handling lands in both regions at once.

The host class provides CCI_API_URL, CCI_DOMAIN_API_URL, CCI_PACKAGE_ID,
the _cci_client_* identity attributes, LANGUAGE, brand and data_timezone.
"""

import datetime as dt
import logging
from typing import Any

import requests

from .cci_fingerprint import (
    build_fp_headers,
    client_name_for_brand,
    compute_x_fingerprint,
    is_gate_rejection,
)
from .const import BRANDS, DOMAIN
from .exceptions import AuthenticationError

_LOGGER = logging.getLogger(__name__)

USER_AGENT_OK_HTTP: str = "okhttp/3.12.0"


class CciAuthMixin:
    """CCI header builders and auth-code / CCS token exchanges."""

    CCI_API_URL: str
    CCI_PACKAGE_ID: str
    LANGUAGE: str
    brand: int
    data_timezone: dt.tzinfo
    _cci_client_name: str
    _cci_client_version: str
    _cci_client_os_version: str
    _cci_notification_provider: str

    @property
    def CCI_DOMAIN_API_URL(self) -> str:
        return self.CCI_API_URL + "/domain/api/"

    def _cci_timezone_offset(self) -> str:
        """Current UTC offset of the data timezone as '+HH:MM'."""
        aware = dt.datetime.now(dt.UTC).astimezone(self.data_timezone)
        off = aware.strftime("%z")  # e.g. +0200
        return f"{off[:3]}:{off[3:]}" if off else "+00:00"

    def _cci_fp_headers(self, device_id: str, body: str = "") -> dict[str, Any]:
        """Header set for the fingerprint-gated CCI endpoints (see #1888).

        The gate validates the X-Fingerprint HMAC against the client identity
        in these headers, so this uses the live-verified OneApp build identity
        (cci_fingerprint module constants), not the legacy CCI header values.
        Pass the exact request body so the signature covers it.
        """
        headers = build_fp_headers(
            package_id=self.CCI_PACKAGE_ID,
            client_name=client_name_for_brand(self.brand),
            device_id=device_id,
            locale=self.LANGUAGE,
            timezone=self._cci_timezone_offset(),
            brand_label=BRANDS[self.brand],
        )
        ts = str(int(dt.datetime.now(dt.UTC).timestamp() * 1000))
        headers["X-Timestamp"] = ts
        headers["X-Fingerprint"] = compute_x_fingerprint(headers, self.brand, body)
        return headers

    def _get_cci_headers(
        self,
        device_id: str,
        cci_access_token: str | None = None,
        non_ccs_token: str | None = None,
        exchangeable_token: str | None = None,
        content_type: str | None = None,
    ) -> dict[str, Any]:
        """Headers for the legacy (unsigned) CCI API routes.

        - Authentication: raw nonCcsToken (no Bearer prefix)
        - authorization: "Bearer " + CCI accessToken
        - exchangeable-token / non-ccs-token (kebab-case)
        """
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

    def _exchange_auth_code_for_cci_tokens(
        self, device_id: str, auth_code: str
    ) -> dict[str, Any]:
        """POST the auth code for CCI tokens and return the token payload.

        All EU OneApp auth endpoints are fingerprint-gated (2026-10-14):
        /auth/v1/token with the signed X-Fingerprint identity headers goes
        first; the legacy domain/api exchange runs as fallback so a gate
        rollback keeps working.
        """
        resp = self._fp_exchange_auth_code(device_id, auth_code)
        if resp.status_code != 200 and is_gate_rejection(resp.status_code, resp.text):
            _LOGGER.error(
                f"{DOMAIN} - Fingerprint gate rejected the signed auth-code "
                f"exchange (HTTP {resp.status_code}); fell back to the legacy "
                "route. This fallback is transitional - report this log so "
                "the fallback can be removed"
            )
            resp = self._legacy_exchange_auth_code(device_id, auth_code)
        if resp.status_code != 200:
            raise AuthenticationError(
                f"CCI token exchange failed: HTTP {resp.status_code} — "
                f"{resp.text[:200]}. This may indicate an API change."
            )
        payload: dict[str, Any] = resp.json()
        return payload

    def _legacy_exchange_auth_code(
        self, device_id: str, auth_code: str
    ) -> requests.Response:
        """POST code to the legacy domain/api exchange (no fingerprint)."""
        headers = self._get_cci_headers(device_id)
        return requests.post(
            f"{self.CCI_DOMAIN_API_URL}v1/auth/token",
            params={"code": auth_code},
            headers=headers,
            timeout=(5, 30),
        )

    def _fp_exchange_auth_code(
        self, device_id: str, auth_code: str
    ) -> requests.Response:
        """POST code to the fingerprint-gated /auth/v1/token exchange."""
        return requests.post(
            f"{self.CCI_API_URL}/auth/v1/token",
            params={"code": auth_code},
            headers=self._cci_fp_headers(device_id),
            timeout=(5, 30),
        )

    def _exchange_ccs_token(
        self,
        device_id: str,
        cci_access_token: str,
        non_ccs_token: str,
        exchangeable_token: str,
    ) -> tuple[str, dt.datetime]:
        """Exchange a CCI access token for a CCS token (token-exchange?serviceType=CCS).

        The token-exchange endpoint is fingerprint-gated like the other /auth
        endpoints (unsigned calls answer 9035, field-verified 2026-10-14 on
        Hyundai): the signed primary carries the CCI token headers on the
        OneApp identity, the legacy domain/api route runs unsigned as
        fallback.

        The CCS token is accepted as a Bearer access_token by the legacy
        ccapi:8080 endpoints (region 1) and the GSPA REST endpoints
        (region 9). Returns (ccs_token, valid_until).
        """
        headers = self._cci_fp_headers(device_id)
        headers["authorization"] = (
            "Bearer " + (cci_access_token or "").removeprefix("Bearer ").strip()
        )
        headers["Authentication"] = non_ccs_token or ""
        if exchangeable_token:
            headers["exchangeable-token"] = exchangeable_token
            headers["non-ccs-token"] = non_ccs_token or ""
        resp = requests.post(
            f"{self.CCI_API_URL}/auth/v1/token-exchange",
            params={"serviceType": "CCS"},
            headers=headers,
            timeout=(5, 30),
        )
        if resp.status_code != 200 and is_gate_rejection(resp.status_code, resp.text):
            _LOGGER.error(
                f"{DOMAIN} - Fingerprint gate rejected the signed CCS "
                f"token-exchange (HTTP {resp.status_code}); fell back to the "
                "legacy route. This fallback is transitional - report this "
                "log so the fallback can be removed"
            )
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
        # expiresTime is the CCS token TTL in seconds (e.g. 86400 = 24h), not an
        # epoch. Treat it as a relative duration from now; fall back to +1h.
        expires_in = data.get("expiresTime")
        if expires_in:
            ccs_valid_until = dt.datetime.now(dt.UTC) + dt.timedelta(
                seconds=int(expires_in)
            )
        else:
            ccs_valid_until = dt.datetime.now(dt.UTC) + dt.timedelta(seconds=3600)
        return ccs_token, ccs_valid_until
