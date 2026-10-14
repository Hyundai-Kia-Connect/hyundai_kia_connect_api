"""cci_fingerprint.py — X-Fingerprint signing for the EU CCI auth endpoints.

The EU OneApp app signs POSTs to cci-api-eu.../auth/* with X-Fingerprint:
HMAC-SHA256 over the client identity plus app-embedded secret constant
pairs. The X-Timestamp is in the message only. The key/message shapes and
the constants below were recovered from the app's Android 1.4.0 build and
verified live on 2026-10-14 for both Hyundai and Kia EU (exchange and
token-refresh answer 200; a wrong signature answers 9036, a missing
X-Fingerprint answers 9035).
"""

from __future__ import annotations

import hashlib
import hmac
import re
import uuid

from .const import BRAND_GENESIS, BRAND_HYUNDAI, BRAND_KIA, BRANDS

# Signing constants per brand: (secret, piece, suffix), concatenated into
# the HMAC key in this order. The pieces rotate with app releases.
_FINGERPRINT_KEYS: dict[str, tuple[str, str, str]] = {
    BRAND_HYUNDAI: ("OsZDfDuCyVX", "yfqjCoA2JbX", "2MrLpGwNBC"),
    BRAND_KIA: ("yb4Z1n2nlKe", "ukNilE7QEdb", "KB34nStPVa"),
}
# Genesis EU is not covered: its login goes through GSPA and the auth-code
# exchange path in GspaApiEU uses this module only as a fallback.
_FINGERPRINT_KEYS[BRAND_GENESIS] = _FINGERPRINT_KEYS[BRAND_HYUNDAI]

# Android OneApp build identity the gate validates the fingerprint against.
CLIENT_VERSION = "1.4.0"
CLIENT_OS_CODE = "android"
CLIENT_OS_VERSION = "14"
CLIENT_DEVICE_MODEL = "SM-S918B"
CLIENT_NOTIFICATION_PROVIDER = "FCM"
USER_AGENT = "okhttp/4.12.0"
APP_REGION = "eu"

_IDENTITY_HEADERS = ("client-id", "client-name", "client-version", "client-os-code")


def compute_x_fingerprint(
    headers: dict[str, str], brand: int, body: str | bytes = ""
) -> str:
    """HMAC-SHA256 over the client identity per the OneApp signing scheme.

    Key: identity (client-id + client-name + client-version + client-os-code)
         + secret + piece + suffix.
    Message: identity + client-os-version + X-Timestamp
         (+ request body with Java-ASCII whitespace removed).
    """
    lower = {k.lower(): v for k, v in headers.items()}
    identity = "".join(lower.get(name, "") for name in _IDENTITY_HEADERS)
    pieces = _FINGERPRINT_KEYS[BRANDS[brand]]
    if isinstance(body, bytes):
        body = body.decode("utf-8")
    msg = (
        identity
        + lower.get("client-os-version", "")
        + lower.get("x-timestamp", "")
        + re.sub(r"\s+", "", body or "", flags=re.ASCII)
    )
    key = identity + "".join(pieces)
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
    """Header set for the fingerprint-gated CCI endpoints."""
    return {
        "client-id": package_id,
        "client-name": client_name,
        "client-version": CLIENT_VERSION,
        "client-os-code": CLIENT_OS_CODE,
        "client-os-version": CLIENT_OS_VERSION,
        "client-device-id": device_id or "",
        "client-device-model": CLIENT_DEVICE_MODEL,
        "client-notification-provider-type": CLIENT_NOTIFICATION_PROVIDER,
        "locale": locale.upper(),
        "timezone": timezone,
        "App-Request-Id": f"{uuid.uuid4().hex[:24].upper()}_"
        f"{(device_id or '').replace('-', '')}_AOS",
        "X-Brand": brand_label,
        "X-Region": APP_REGION,
        "User-Agent": USER_AGENT,
        "Accept": "*/*",
        "Accept-Language": locale.lower(),
    }
