# hyundai_token.py
import os
import re
import time
import shutil
import sys
import requests
import platform
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.common.exceptions import WebDriverException
import chromedriver_autoinstaller

# Detect OS
IS_WINDOWS = platform.system() == "Windows"

# Chrome options (no printing)
options = Options()
options.add_argument("--window-size=1000,800")
options.add_argument("--disable-blink-features=AutomationControlled")
options.add_argument(
    "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36_CCS_APP_AOS"
)


def install_driver():
    """
    Ensure a matching chromedriver is installed and return its path.
    Exit with an informative message if Chrome is not found.
    """
    try:
        _ = chromedriver_autoinstaller.get_chrome_version()
    except Exception:
        print(
            "ERROR: Google Chrome not found. Please install Google Chrome and try again."
        )
        sys.exit(1)

    try:
        driver_path = chromedriver_autoinstaller.install()
        return driver_path
    except Exception:
        return None


def safe_install_and_start():
    """
    Install chromedriver (or reinstall after cleanup) and start the Chrome WebDriver.
    Attempts one automatic cleanup/reinstall if the first start fails.
    """
    driver_path = install_driver()
    if not driver_path:
        try:
            driver_path = chromedriver_autoinstaller.install()
        except Exception as e:
            print(f"ERROR: Failed to install chromedriver: {e}")
            sys.exit(1)

    try:
        service = Service(driver_path)
        driver = webdriver.Chrome(service=service, options=options)
        return driver
    except WebDriverException:
        # Attempt to remove the installed driver folder and try reinstall
        try:
            driver_dir = os.path.dirname(driver_path) if driver_path else None
            if driver_dir and os.path.exists(driver_dir):
                shutil.rmtree(driver_dir, ignore_errors=True)
        except Exception:
            pass

        # Reinstall and try again
        try:
            driver_path = chromedriver_autoinstaller.install()
            service = Service(driver_path)
            driver = webdriver.Chrome(service=service, options=options)
            return driver
        except Exception as final_err:
            print("ERROR: Could not start Chrome WebDriver after reinstall.")
            print("Reason:", final_err)
            sys.exit(1)


# Start: install driver and launch browser without printing Chrome version
driver = safe_install_and_start()

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

driver.get(LOGIN_URL)

print("\n" + "=" * 60)
print("LOGIN REQUIRED:")
print("Please complete login and reCAPTCHA in the opened Chrome window.")
print("=" * 60)
input("\nPress ENTER after login is complete...")

# After login, get the authorization code from redirected URL
driver.get(
    f"{BASE_URL}authorize?response_type=code&client_id=6d477c38-3ca4-4cf3-9557-2a1929a94654&redirect_uri=https://prd.eu-ccapi.hyundai.com:8080/api/v1/user/oauth2/token&lang=en&state=ccsp"
)
time.sleep(2)

match = re.search(r"code=([^&]+)", driver.current_url)
if match:
    code = match.group(1)
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

driver.quit()
input("\nPress Enter to exit...")
