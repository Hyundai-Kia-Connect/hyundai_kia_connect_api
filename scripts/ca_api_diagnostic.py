#!/usr/bin/env python3
"""
CA API diagnostic script for Hyundai/Kia Canada token refresh investigation.

Issue: https://github.com/Hyundai-Kia-Connect/kia_uvo/issues/1715
Purpose: Test various CA API endpoints and approaches to find a working
         token refresh mechanism that doesn't require OTP every few days.

Usage:
    python ca_api_diagnostic.py --brand hyundai --username EMAIL --password PASS --pin PIN

What it tests:
    1. /tods/api/v2/login with deterministic device_id (current approach)
    2. /tods/api/lgn (old login endpoint — may not require OTP)
    3. /tods/api/vrfytnc (verify token — may extend session)
    4. /tods/api/vrfyacctkn (verify account token — unknown purpose)
    5. Potential refresh endpoints: /v2/refresh, /v2/token, /v2/refreshtkn
    6. refreshToken passed as header/body to v2/login (like USA rmtoken)
    7. Device ID stability across logins

Safe: This script only reads data. It does not send commands to your vehicle.
"""

import argparse
import base64
import platform
import socket
import time
import uuid

import requests
import requests.packages.urllib3.util.connection as urllib3_cn


# Force IPv4 — CA API has known IPv6 issues
def allowed_gai_family():
    return socket.AF_INET


urllib3_cn.allowed_gai_family = allowed_gai_family

# ── Brand config ────────────────────────────────────────────────────────────

BRAND_CONFIG = {
    "hyundai": {
        "base_url": "mybluelink.ca",
        "client_id": "HATAHSPACA0232141ED9722C67715A0B",
        "client_secret": "CLISCR01AHSPA",
    },
    "kia": {
        "base_url": "kiaconnect.ca",
        "client_id": "KIAHSPACA0232141ED9722C67715A0B",
        "client_secret": "CLISCRKA0232141ED9722C67715A0B",
    },
    "genesis": {
        "base_url": "genesisconnect.ca",
        "client_id": "GENHSPACA0232141ED9722C67715A0B",
        "client_secret": "CLISCRGE0232141ED9722C67715A0B",
    },
}


def get_device_id() -> str:
    """Deterministic device ID — same as KiaUvoApiCA._get_device_id()"""
    device_uuid = uuid.uuid5(
        uuid.NAMESPACE_DNS, f"{uuid.getnode():x}-{platform.node() or ''}"
    )
    return base64.b64encode(device_uuid.hex.encode()).decode()


def get_random_device_id() -> str:
    """Random device ID — to test if server treats new devices differently"""
    return base64.b64encode(uuid.uuid4().hex.encode()).decode()


def make_headers(brand_config: dict, device_id: str, access_token: str = None) -> dict:
    base_url = brand_config["base_url"]
    headers = {
        "User-Agent": "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Mobile Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-CA,en-US;q=0.8,en;q=0.5,fr;q=0.3",
        "Accept-Encoding": "gzip, deflate, br, zstd",
        "Content-Type": "application/json;charset=UTF-8",
        "from": "CWP",
        "offset": "-5",
        "language": "0",
        "Origin": f"https://{base_url}",
        "Connection": "keep-alive",
        "Referer": f"https://{base_url}/login",
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
        "Priority": "u=0",
        "Pragma": "no-cache",
        "Cache-Control": "no-cache",
        "client_id": brand_config["client_id"],
        "client_secret": brand_config["client_secret"],
        "Deviceid": device_id,
    }
    if access_token:
        headers["accessToken"] = access_token
    return headers


def safe_json(response: requests.Response) -> dict | None:
    """Parse JSON safely, print raw text on failure."""
    try:
        return response.json()
    except Exception:
        print(f"  [RAW] Status {response.status_code}: {response.text[:500]}")
        return None


def print_result(label: str, response: requests.Response):
    """Print a structured test result."""
    data = safe_json(response)
    code = response.status_code
    if data:
        resp_code = data.get("responseHeader", {}).get("responseCode", "?")
        error = data.get("error", {})
        err_code = error.get("errorCode", "") if isinstance(error, dict) else ""
        err_desc = (
            error.get("errorDesc", "") if isinstance(error, dict) else str(error)[:100]
        )
        result_keys = (
            list(data.get("result", {}).keys())[:5]
            if isinstance(data.get("result"), dict)
            else str(data.get("result", ""))[:80]
        )
        print(
            f"  [{label}] HTTP={code} respCode={resp_code} errCode={err_code} errDesc={err_desc[:60]} result_keys={result_keys}"
        )
    else:
        print(f"  [{label}] HTTP={code} (non-JSON or empty)")


# ── Test functions ──────────────────────────────────────────────────────────


def test_v2_login(
    base_url: str, headers: dict, username: str, password: str
) -> dict | None:
    """Test 1: Current v2/login endpoint with deterministic device_id."""
    print("\n=== Test 1: POST /tods/api/v2/login (current approach) ===")
    url = f"https://{base_url}/tods/api/v2/login"
    data = {"loginId": username, "password": password}
    try:
        resp = requests.post(url, json=data, headers=headers, timeout=30)
        print_result("v2/login", resp)
        data = safe_json(resp)
        if data and data.get("responseHeader", {}).get("responseCode") == 0:
            token_data = data.get("result", {}).get("token", {})
            print(f"  accessToken: ...{token_data.get('accessToken', '')[-20:]}")
            print(f"  refreshToken: ...{token_data.get('refreshToken', '')[-20:]}")
            print(f"  expireIn: {token_data.get('expireIn')}")
            return token_data
        elif data and data.get("error", {}).get("errorCode") == "7110":
            print("  >>> OTP REQUIRED (7110) — device not recognized by server")
        return None
    except Exception as e:
        print(f"  [v2/login] ERROR: {e}")
        return None


def test_lgn_login(
    base_url: str, headers: dict, username: str, password: str
) -> dict | None:
    """Test 2: Old /tods/api/lgn endpoint (bluelinky uses this)."""
    print("\n=== Test 2: POST /tods/api/lgn (old endpoint, no MFA) ===")
    url = f"https://{base_url}/tods/api/lgn"
    data = {"loginId": username, "password": password}
    try:
        resp = requests.post(url, json=data, headers=headers, timeout=30)
        print_result("lgn", resp)
        data = safe_json(resp)
        if data and data.get("responseHeader", {}).get("responseCode") == 0:
            result = data.get("result", {})
            print(f"  accessToken: ...{result.get('accessToken', '')[-20:]}")
            print(f"  refreshToken: ...{result.get('refreshToken', '')[-20:]}")
            return result
        return None
    except Exception as e:
        print(f"  [lgn] ERROR: {e}")
        return None


def test_v2_login_with_refresh_token(
    base_url: str, headers: dict, username: str, password: str, refresh_token: str
) -> None:
    """Test 3: v2/login with refreshToken in body (like USA rmtoken)."""
    print("\n=== Test 3: POST /tods/api/v2/login with refreshToken in body ===")
    url = f"https://{base_url}/tods/api/v2/login"

    # 3a: refreshToken in request body
    data = {"loginId": username, "password": password, "refreshToken": refresh_token}
    try:
        resp = requests.post(url, json=data, headers=headers, timeout=30)
        print_result("v2/login+body.refreshToken", resp)
    except Exception as e:
        print(f"  [3a] ERROR: {e}")

    # 3b: refreshToken as header
    headers_rt = headers.copy()
    headers_rt["refreshToken"] = refresh_token
    data = {"loginId": username, "password": password}
    try:
        resp = requests.post(url, json=data, headers=headers_rt, timeout=30)
        print_result("v2/login+header.refreshToken", resp)
    except Exception as e:
        print(f"  [3b] ERROR: {e}")


def test_potential_refresh_endpoints(
    base_url: str, headers: dict, refresh_token: str
) -> None:
    """Test 4: Probe potential refresh token endpoints."""
    print("\n=== Test 4: Probe potential refresh endpoints ===")
    endpoints = [
        "/tods/api/v2/refresh",
        "/tods/api/v2/token",
        "/tods/api/v2/refreshtkn",
        "/tods/api/refreshtoken",
        "/tods/api/rfrshtkn",
        "/tods/api/token/refresh",
    ]
    for endpoint in endpoints:
        url = f"https://{base_url}{endpoint}"
        for body_fmt in [
            {"refreshToken": refresh_token},
            {"grant_type": "refresh_token", "refresh_token": refresh_token},
        ]:
            try:
                resp = requests.post(url, json=body_fmt, headers=headers, timeout=15)
                code = resp.status_code
                data = safe_json(resp)
                if data:
                    resp_code = data.get("responseHeader", {}).get("responseCode", "?")
                    err = data.get("error", {})
                    err_desc = (
                        err.get("errorDesc", "")[:60]
                        if isinstance(err, dict)
                        else str(err)[:60]
                    )
                    print(
                        f"  [{endpoint}] HTTP={code} respCode={resp_code} err={err_desc}"
                    )
                else:
                    print(f"  [{endpoint}] HTTP={code} (non-JSON)")
            except requests.exceptions.ConnectionError:
                print(f"  [{endpoint}] CONNECTION REFUSED")
            except Exception as e:
                print(f"  [{endpoint}] ERROR: {e}")


def test_verify_endpoints(base_url: str, headers: dict, access_token: str) -> None:
    """Test 5: Verify token endpoints (may extend session)."""
    print("\n=== Test 5: Verify token endpoints ===")

    # 5a: vrfytnc — "verify token"
    url = f"https://{base_url}/tods/api/vrfytnc"
    try:
        resp = requests.post(url, json={}, headers=headers, timeout=15)
        print_result("vrfytnc", resp)
    except Exception as e:
        print(f"  [vrfytnc] ERROR: {e}")

    # 5b: vrfyacctkn — "verify account token"
    url = f"https://{base_url}/tods/api/vrfyacctkn"
    try:
        resp = requests.post(url, json={}, headers=headers, timeout=15)
        print_result("vrfyacctkn", resp)
    except Exception as e:
        print(f"  [vrfyacctkn] ERROR: {e}")

    # 5c: vhcllst — current test_token approach (may keep session alive)
    url = f"https://{base_url}/tods/api/vhcllst"
    try:
        resp = requests.post(url, json={}, headers=headers, timeout=15)
        print_result("vhcllst", resp)
    except Exception as e:
        print(f"  [vhcllst] ERROR: {e}")


def test_device_id_stability(
    base_url: str, brand_config: dict, username: str, password: str
) -> None:
    """Test 6: Login twice with same device_id, check if OTP required second time."""
    print("\n=== Test 6: Device ID stability — login twice with same device_id ===")
    device_id = get_device_id()
    print(f"  Device ID: {device_id}")

    # First login
    headers1 = make_headers(brand_config, device_id)
    url = f"https://{base_url}/tods/api/v2/login"
    data = {"loginId": username, "password": password}
    try:
        resp1 = requests.post(url, json=data, headers=headers1, timeout=30)
        data1 = safe_json(resp1)
        if data1:
            rc = data1.get("responseHeader", {}).get("responseCode", "?")
            err = data1.get("error", {})
            err_code = err.get("errorCode", "") if isinstance(err, dict) else ""
            print(f"  Login 1: respCode={rc} errCode={err_code}")
            if err_code == "7110":
                print(
                    "  >>> First login requires OTP — this is expected for a new device_id"
                )
                print("  >>> Cannot test stability without completing OTP flow")
                return
    except Exception as e:
        print(f"  [Login 1] ERROR: {e}")
        return

    # Wait a moment
    print("  Waiting 5 seconds...")
    time.sleep(5)

    # Second login with same device_id
    headers2 = make_headers(brand_config, device_id)
    try:
        resp2 = requests.post(url, json=data, headers=headers2, timeout=30)
        data2 = safe_json(resp2)
        if data2:
            rc = data2.get("responseHeader", {}).get("responseCode", "?")
            err = data2.get("error", {})
            err_code = err.get("errorCode", "") if isinstance(err, dict) else ""
            print(f"  Login 2: respCode={rc} errCode={err_code}")
            if rc == 0:
                print(
                    "  >>> Second login succeeded WITHOUT OTP — device is remembered!"
                )
            elif err_code == "7110":
                print(
                    "  >>> Second login STILL requires OTP — device NOT remembered after first login"
                )
            else:
                print("  >>> Unexpected response on second login")
    except Exception as e:
        print(f"  [Login 2] ERROR: {e}")


def test_different_device_id(
    base_url: str, brand_config: dict, username: str, password: str
) -> None:
    """Test 7: Login with random device_id — should always trigger OTP."""
    print("\n=== Test 7: Login with random device_id (should trigger OTP) ===")
    random_id = get_random_device_id()
    print(f"  Random Device ID: {random_id}")
    headers = make_headers(brand_config, random_id)
    url = f"https://{base_url}/tods/api/v2/login"
    data = {"loginId": username, "password": password}
    try:
        resp = requests.post(url, json=data, headers=headers, timeout=30)
        data = safe_json(resp)
        if data:
            rc = data.get("responseHeader", {}).get("responseCode", "?")
            err = data.get("error", {})
            err_code = err.get("errorCode", "") if isinstance(err, dict) else ""
            print(f"  respCode={rc} errCode={err_code}")
            if err_code == "7110":
                print("  >>> Random device_id triggers OTP as expected")
            elif rc == 0:
                print("  >>> UNEXPECTED: Random device_id did NOT trigger OTP!")
    except Exception as e:
        print(f"  ERROR: {e}")


# ── Main ────────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(
        description="CA API diagnostic for token refresh investigation"
    )
    parser.add_argument(
        "--brand",
        required=True,
        choices=["hyundai", "kia", "genesis"],
        help="Vehicle brand",
    )
    parser.add_argument("--username", required=True, help="Account email")
    parser.add_argument("--password", required=True, help="Account password")
    parser.add_argument(
        "--pin", default="", help="Vehicle PIN (not used in this script)"
    )
    parser.add_argument(
        "--skip-double-login",
        action="store_true",
        help="Skip the double-login test (avoids 2 login calls)",
    )
    parser.add_argument(
        "--skip-probe",
        action="store_true",
        help="Skip probing unknown endpoints (avoids many 404s)",
    )
    args = parser.parse_args()

    brand_config = BRAND_CONFIG[args.brand]
    base_url = brand_config["base_url"]
    device_id = get_device_id()

    print("CA API Diagnostic — Issue #1715")
    print(f"Brand: {args.brand}")
    print(f"Base URL: https://{base_url}")
    print(f"Device ID: {device_id}")
    print(f"Timestamp: {time.strftime('%Y-%m-%d %H:%M:%S %Z')}")
    print("=" * 60)

    # ── Phase 1: Login and get tokens ───────────────────────────────────

    headers = make_headers(brand_config, device_id)
    token_data = test_v2_login(base_url, headers, args.username, args.password)

    if not token_data:
        # If v2/login with deterministic device_id fails (OTP required),
        # try the old /lgn endpoint which bluelinky uses
        print("\nv2/login failed or requires OTP. Trying old /lgn endpoint...")
        lgn_result = test_lgn_login(base_url, headers, args.username, args.password)
        if lgn_result:
            token_data = lgn_result
    else:
        # Also test /lgn for comparison
        test_lgn_login(base_url, headers, args.username, args.password)

    # ── Phase 2: If we have tokens, test refresh approaches ─────────────

    if token_data:
        access_token = token_data.get("accessToken", "")
        refresh_token = token_data.get("refreshToken", "")

        # Test refresh token in various ways
        if refresh_token:
            test_v2_login_with_refresh_token(
                base_url, headers, args.username, args.password, refresh_token
            )

        # Test verify endpoints
        if access_token:
            auth_headers = make_headers(brand_config, device_id, access_token)
            test_verify_endpoints(base_url, auth_headers, access_token)

        # Probe potential refresh endpoints
        if refresh_token and not args.skip_probe:
            test_potential_refresh_endpoints(base_url, headers, refresh_token)
        elif args.skip_probe:
            print("\n=== Test 4: SKIPPED (use --skip-probe to avoid) ===")

    else:
        print("\n!!! No tokens obtained — cannot test refresh approaches.")
        print("!!! If OTP was required, you can:")
        print("!!! 1. Complete the OTP flow in the kia_uvo integration")
        print("!!! 2. Wait for device to be remembered (up to 90 days)")
        print("!!! 3. Re-run this script — it should work without OTP then")

    # ── Phase 3: Device ID stability ────────────────────────────────────

    if not args.skip_double_login:
        test_device_id_stability(base_url, brand_config, args.username, args.password)
    else:
        print("\n=== Test 6: SKIPPED (--skip-double-login) ===")

    # Random device ID test
    test_different_device_id(base_url, brand_config, args.username, args.password)

    print("\n" + "=" * 60)
    print("Diagnostic complete. Please share the output in the GitHub issue:")
    print("https://github.com/Hyundai-Kia-Connect/kia_uvo/issues/1715")
    print("\nNOTE: Review output for any tokens/credentials before sharing!")
    print("      Redact accessToken, refreshToken, and any personal info.")


if __name__ == "__main__":
    main()
