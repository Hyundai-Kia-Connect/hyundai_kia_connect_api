# hyundai_token.py
import re
import subprocess
import sys
import time

import requests
from playwright.sync_api import sync_playwright


def ensure_browser_installed():
    """Install Chromium if not already downloaded by Playwright."""
    try:
        subprocess.run(
            [sys.executable, "-m", "playwright", "install", "chromium"],
            check=True,
            capture_output=True,
        )
    except subprocess.CalledProcessError as e:
        print("ERROR: Could not install Chromium browser.")
        print(f"Details: {e.stderr.decode()}")
        sys.exit(1)


def main():
    ensure_browser_installed()

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=False,
            args=[
                "--window-size=1000,800",
                "--disable-blink-features=AutomationControlled",
            ],
        )
        context = browser.new_context(
            viewport={"width": 1000, "height": 800},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0.0.0 Safari/537.36_CCS_APP_AOS"
            ),
        )
        page = context.new_page()

        # Hyundai login URL (ENGLISH VERSION)
        BASE_URL = "https://idpconnect-eu.hyundai.com/auth/api/v2/user/oauth2/"
        LOGIN_URL = (
            f"{BASE_URL}authorize?"
            "client_id=peuhyundaiidm-ctb&"
            "redirect_uri=https%3A%2F%2Fctbapi.hyundai-europe.com%2Fapi%2Fauth&"
            "nonce=&state=EN_&"
            "scope=openid+profile+email+phone&"
            "response_type=code&"
            "connector_client_id=peuhyundaiidm-ctb&"
            "connector_scope=&connector_session_key=&country=&captcha=1&"
            "ui_locales=en-US&lang=en"
        )

        page.goto(LOGIN_URL)

        print("\n" + "=" * 60)
        print("LOGIN REQUIRED:")
        print("Please complete login and reCAPTCHA in the opened browser window.")
        print("=" * 60)
        input("\nPress ENTER after login is complete...")

        # Use CDP to intercept the redirect that carries the authorization code.
        # The redirect target (prd.eu-ccapi.hyundai.com) is unreachable, so we
        # capture the code from the network request before it times out.
        print("Fetching authorization code...")

        authorize_url = (
            f"{BASE_URL}authorize?response_type=code"
            "&client_id=6d477c38-3ca4-4cf3-9557-2a1929a94654"
            "&redirect_uri=https://prd.eu-ccapi.hyundai.com:8080/api/v1/user/oauth2/token"
            "&lang=en&state=ccsp"
        )

        page.goto(authorize_url)

        print("A consent/authorization page may appear in the browser.")
        print("If prompted, please complete any required steps.")
        print("Waiting for authorization code (up to 120s)...")

        # Wait for the OAuth flow to complete — the final redirect carries code=
        try:
            page.wait_for_url(re.compile(r"code="), timeout=120000)
        except Exception:
            pass

        code = None
        code_match = re.search(r"[?&]code=([^&]+)", page.url)
        if code_match:
            code = code_match.group(1)
        if code:
            response = requests.post(
                f"{BASE_URL}token",
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": "https://prd.eu-ccapi.hyundai.com:8080/api/v1/user/oauth2/token",
                    "client_id": "6d477c38-3ca4-4cf3-9557-2a1929a94654",
                    "client_secret": "KUy49XxPzLpLuoK0xhBC77W6VXhmtQR9iQhmIFjjoY4IpxsV",
                },
            )
            if response.status_code == 200:
                token = response.json().get("refresh_token")
                print("\n" + "=" * 60)
                print(f"REFRESH TOKEN:\n{token}\n")
                print(
                    "Use this token as your password in your Hyundai integration in Home Assistant."
                )
                print("=" * 60)
            else:
                print(f"Error while retrieving token: {response.text}")
        else:
            print("Authorization code not found in URL. Try logging in again.")

        browser.close()

    input("\nPress Enter to exit...")


if __name__ == "__main__":
    main()
