"""cci_fingerprint.py — X-Fingerprint signing for the fingerprint-gated EU
CCI endpoints (live-verified 2026-10-13 against prd.eu-ccapi/cci-api-eu for
kia_uvo #1888).

The EU OneApp app signs requests to cci-api-eu with an X-Fingerprint header:
HMAC-SHA256 over request-header values, as described in compute_x_fingerprint.
The app-embedded secret pair below rotates with app releases; when the app
ships a new build identity the constants here need the same update.

Live-verified endpoints (Hyundai EU, 2026-10-13):
- exchange: cci-api-eu.../auth/v1/token?code=...  → 200 with the legacy
  app-shaped header set; domain/api/v1/auth/token rejects with 400/9013.
- refresh: cci-api-eu.../auth/v2/token-refresh     → 200 with the same
  identity; domain/api/v2/auth/token-refresh rejects with 400/9013.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import uuid

from .const import BRANDS

# Live-verified OneApp build identity for the fingerprint-gated endpoints.
# 9013 is returned for other client identities (e.g. app 1.1.5 era values).
CLIENT_VERSION = "1.4.1"
OS_VERSION = "27.2.0"
CLIENT_OS_CODE = "ios"
BUILD = "1675"
APP_REGION = "eu"

_HMAC_SECRET = "OsZDfDuCyVX"
_HMAC_KEY_SUFFIX = "2MrLpGwNBC"


def compute_x_fingerprint(headers: dict[str, str], body: str = "") -> str:
    """HMAC-SHA256 over request header values, per the OneApp signing scheme.

    Key: client-id + client-name + client-version + client-os-code
         + x-timestamp + secret + suffix.
    Message: client-id + client-name + client-version + client-os-code
         + client-os-version + x-timestamp (+ regex-stripped body).
    The X-Timestamp value is read from headers; X-Fingerprint and X-Timestamp
    are excluded from the lowercase copy used for lookup.
    """
    ts = headers.get("X-Timestamp") or headers.get("x-timestamp") or ""
    lower = {k.lower(): v for k, v in headers.items()}
    key = (
        lower["client-id"]
        + lower["client-name"]
        + lower["client-version"]
        + lower["client-os-code"]
        + ts
        + _HMAC_SECRET
        + _HMAC_KEY_SUFFIX
    )
    msg = (
        lower["client-id"]
        + lower["client-name"]
        + lower["client-version"]
        + lower["client-os-code"]
        + lower["client-os-version"]
        + ts
        + (re.sub(r"\s+", "", body) if body else "")
    )
    return hmac.new(key.encode(), msg.encode(), hashlib.sha256).hexdigest()


def client_name_for_brand(brand: int) -> str:
    """'HyundaiEU' / 'KiaEU' / 'GenesisEU' per the OneApp client-name scheme."""
    return f"{BRANDS[brand]}EU"


def build_fp_headers(
    package_id: str,
    client_name: str,
    device_id: str,
    locale: str,
    timezone: str,
    brand_label: str,
) -> dict[str, str]:
    """Header set verified live against the fingerprint-gated endpoints."""
    ua = (
        f"{client_name}/{CLIENT_VERSION} ({package_id}; build:{BUILD}; "
        f"iOS {OS_VERSION}) Alamofire/5.12.0"
    )
    return {
        "client-id": package_id,
        "client-name": client_name,
        "client-version": CLIENT_VERSION,
        "client-os-code": CLIENT_OS_CODE,
        "client-os-version": OS_VERSION,
        "client-device-id": device_id or "",
        "client-device-model": "iPhone",
        "client-notification-provider-type": "IOS_APPSTORE",
        "locale": locale.upper(),
        "timezone": timezone,
        "App-Request-Id": str(uuid.uuid4()),
        "X-Brand": brand_label,
        "X-Region": APP_REGION,
        "User-Agent": ua,
        "Accept": "*/*",
        "Accept-Language": locale.lower(),
    }


def is_fingerprint_gate(resp) -> bool:
    """True when the legacy endpoint answered with the 400/9013 validation
    gate that the fingerprint-gated endpoints supersede."""
    return getattr(resp, "status_code", None) == 400 and "9013" in (
        getattr(resp, "text", "") or ""
    )
