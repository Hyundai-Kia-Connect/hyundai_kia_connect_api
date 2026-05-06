"""Headless EU Kia/Hyundai login using curl_cffi (Android TLS fingerprint).

No browser needed — pure HTTP requests. Extracted from
https://github.com/TMA84/bluelink-refresh-token
"""

import base64
from dataclasses import dataclass
from urllib.parse import urlparse, parse_qs

try:
    from curl_cffi import requests as curl_requests
except ImportError:
    curl_requests = None

try:
    from Crypto.PublicKey import RSA
    from Crypto.Cipher import PKCS1_v1_5
except ImportError:
    RSA = None
    PKCS1_v1_5 = None

from .const import BRANDS
from .exceptions import AuthenticationError

_MOBILE_UA = (
    "Mozilla/5.0 (Linux; Android 4.1.1; Galaxy Nexus Build/JRO03C) "
    "AppleWebKit/535.19 (KHTML, like Gecko) Chrome/18.0.1025.166 "
    "Mobile Safari/535.19_CCS_APP_AOS"
)

_BRAND_OAUTH = {
    # Keys are brand int constants: 1=Kia, 2=Hyundai, 3=Genesis (see BRANDS in const.py)
    1: {
        "client_id": "fdc85c00-0a2f-4c64-bcb4-2cfb1500730a",
        "client_secret": "secret",
        "host": "https://idpconnect-eu.kia.com",
        "redirect_uri": (
            "https://prd.eu-ccapi.kia.com:8080/api/v1/user/oauth2/redirect"
        ),
    },
    2: {
        "client_id": "6d477c38-3ca4-4cf3-9557-2a1929a94654",
        "client_secret": "KUy49XxPzLpLuoK0xhBC77W6VXhmtQR9iQhmIFjjoY4IpxsV",
        "host": "https://idpconnect-eu.hyundai.com",
        "redirect_uri": (
            "https://prd.eu-ccapi.hyundai.com:8080/api/v1/user/oauth2/token"
        ),
    },
    3: {
        "client_id": "3020afa2-30ff-412a-aa51-d28fbe901e10",
        "client_secret": "secret",
        "host": "https://idpconnect-eu.genesis.com",
        "redirect_uri": (
            "https://prd-eu-ccapi.genesis.com/api/v1/user/oauth2/redirect"
        ),
    },
}


@dataclass
class BluelinkToken:
    """Token result from headless login."""

    access_token: str
    refresh_token: str
    expires_in: int


def get_token(username: str, password: str, brand: int) -> BluelinkToken:
    """Generate access/refresh tokens from username and password.

    Uses curl_cffi to impersonate an Android Chrome TLS fingerprint,
    matching the official Kia/Hyundai/Genesis app's authentication flow.

    Args:
        username: Account email.
        password: Account password.
        brand: Brand constant (BRAND_KIA, BRAND_HYUNDAI, or BRAND_GENESIS).

    Returns:
        BluelinkToken with access_token, refresh_token, and expires_in.

    Raises:
        AuthenticationError: If login fails.
        ValueError: If brand is not supported.
    """
    if curl_requests is None:
        raise AuthenticationError(
            "Headless login requires the 'EU' extra. "
            "Install with: pip install hyundai_kia_connect_api[EU]"
        )

    if brand not in _BRAND_OAUTH:
        raise ValueError(
            f"Brand {BRANDS.get(brand, brand)} not supported for headless login. "
            f"Supported brands: Kia (1), Hyundai (2), Genesis (3)"
        )

    config = _BRAND_OAUTH[brand]
    host = config["host"]
    client_id = config["client_id"]
    client_secret = config["client_secret"]
    redirect_uri = config["redirect_uri"]

    s = curl_requests.Session(impersonate="chrome131_android")
    s.headers.update({"User-Agent": _MOBILE_UA})

    # Step 1: Load authorize page to get session cookies
    auth_url = (
        f"{host}/auth/api/v2/user/oauth2/authorize"
        f"?response_type=code&client_id={client_id}"
        f"&redirect_uri={redirect_uri}&lang=en&state=ccsp&country=de"
    )
    s.get(auth_url, allow_redirects=True)

    # Step 2: Get RSA public key for password encryption
    resp = s.get(f"{host}/auth/api/v1/accounts/certs")
    if resp.status_code != 200:
        raise AuthenticationError(f"Failed to fetch RSA certs: HTTP {resp.status_code}")
    jwk = resp.json().get("retValue", {})
    kid = jwk.get("kid", "")

    # Convert JWK to RSA key
    n_bytes = base64.urlsafe_b64decode(jwk["n"] + "==")
    e_bytes = base64.urlsafe_b64decode(jwk["e"] + "==")
    n = int.from_bytes(n_bytes, "big")
    e = int.from_bytes(e_bytes, "big")
    key = RSA.construct((n, e))
    cipher = PKCS1_v1_5.new(key)
    encrypted_pw = cipher.encrypt(password.encode("utf-8")).hex()

    # Step 3: POST signin with encrypted password
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
            f"Signin failed: HTTP {resp.status_code} — {resp.text[:300]}"
        )

    location = resp.headers.get("location", "")
    code_list = parse_qs(urlparse(location).query).get("code")
    if not code_list:
        if "error" in location.lower():
            error_desc = parse_qs(urlparse(location).query).get(
                "error_description", ["unknown"]
            )[0]
            raise AuthenticationError(f"Signin rejected: {error_desc}")
        if "/web/v1/user/authorization" in location:
            raise AuthenticationError(
                "Signin succeeded but Kia/Hyundai requires a consent page "
                "(SPA redirect). This may indicate a changed auth flow — "
                "try using a refresh token instead."
            )
        if "authorize" in location:
            raise AuthenticationError(
                "Signin failed — redirected back to login page. "
                "Check username and password."
            )
        raise AuthenticationError(
            f"No authorization code in redirect: {location[:250]}"
        )

    code = code_list[0]

    # Step 4: Exchange authorization code for tokens
    resp = curl_requests.post(
        f"{host}/auth/api/v2/user/oauth2/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": client_id,
            "client_secret": client_secret,
        },
    )

    if resp.status_code != 200:
        raise AuthenticationError(
            f"Token exchange failed: HTTP {resp.status_code} — {resp.text[:200]}"
        )

    tokens = resp.json()

    return BluelinkToken(
        access_token=tokens["token_type"] + " " + tokens["access_token"],
        refresh_token=tokens["refresh_token"],
        expires_in=int(tokens.get("expires_in", 86400)),
    )
