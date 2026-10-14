"""Kia EU Android 1.4.0 CCI request signing; no account credentials."""

import hashlib
import hmac
import re
import time
from urllib.parse import urlsplit

_KEY_PIECES = ("yb4Z1n2nlKe", "ukNilE7QEdb", "KB34nStPVa")
_HOST = "cci-api-eu.kia.com"
_AUTH_PATHS = {
    "/auth/v1/token",
    "/domain/api/v1/auth/token-exchange",
    "/domain/api/v2/auth/token-refresh",
}


def _signature(headers, body, timestamp):
    lower = {name.lower(): value for name, value in headers.items()}
    identity = "".join(
        lower.get(name, "")
        for name in ("client-id", "client-name", "client-version", "client-os-code")
    )
    key = identity + "".join(_KEY_PIECES)
    if isinstance(body, bytes):
        body = body.decode("utf-8")
    message = (
        identity
        + lower.get("client-os-version", "")
        + str(timestamp)
        + re.sub(r"\s+", "", body or "", flags=re.ASCII)
    )
    return hmac.new(
        key.encode("utf-8"), message.encode("utf-8"), hashlib.sha256
    ).hexdigest()


def _post_signed_auth(http, url, **kwargs):
    """Sign and send a Kia CCI auth request without changing its route.

    Callers choose the endpoint explicitly. Serialize the body once so the
    signed bytes are exactly the bytes sent by requests.
    """
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname != _HOST
        or parsed.path not in _AUTH_PATHS
    ):
        return http.post(url, **kwargs)
    headers = http.structures.CaseInsensitiveDict(kwargs.pop("headers", {}) or {})
    headers.update(
        {
            "client-id": "com.kia.oneapp.eu",
            "client-name": "KiaEU",
            "client-version": "1.4.0",
            "client-os-code": "android",
            "client-os-version": "14",
            "client-device-model": "SM-S918B",
            "client-notification-provider-type": "FCM",
            "User-Agent": "okhttp/4.12.0",
        }
    )
    headers.pop("X-Fingerprint", None)
    headers.pop("X-Timestamp", None)
    request = http.Request(
        "POST",
        url,
        headers=headers,
        data=kwargs.pop("data", None),
        json=kwargs.pop("json", None),
    )
    prepared = request.prepare()
    timestamp = int(time.time() * 1000)
    prepared.headers["X-Timestamp"] = str(timestamp)
    prepared.headers["X-Fingerprint"] = _signature(
        prepared.headers, prepared.body, timestamp
    )
    return http.post(url, headers=prepared.headers, data=prepared.body, **kwargs)
