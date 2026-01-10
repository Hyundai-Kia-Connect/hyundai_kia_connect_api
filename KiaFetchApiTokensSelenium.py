# main.py
import re
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException
import requests

session = requests.Session()
CLIENT_ID = "fdc85c00-0a2f-4c64-bcb4-2cfb1500730a"
BASE_URL = "https://idpconnect-eu.kia.com/auth/api/v2/user/oauth2/"
LOGIN_URL = f"{BASE_URL}authorize?ui_locales=de&scope=openid%20profile%20email%20phone&response_type=code&client_id=peukiaidm-online-sales&redirect_uri=https://www.kia.com/api/bin/oneid/login&state=aHR0cHM6Ly93d3cua2lhLmNvbTo0NDMvZGUvP21zb2NraWQ9MjM1NDU0ODBmNmUyNjg5NDIwMmU0MDBjZjc2OTY5NWQmX3RtPTE3NTYzMTg3MjY1OTImX3RtPTE3NTYzMjQyMTcxMjY=_default"
SUCCESS_ELEMENT_SELECTOR = "a[class='logout user']"
REDIRECT_URL_FINAL = "https://prd.eu-ccapi.kia.com:8080/api/v1/user/oauth2/redirect"
REDIRECT_URL = f"{BASE_URL}authorize?response_type=code&client_id={CLIENT_ID}&redirect_uri={REDIRECT_URL_FINAL}&lang=de&state=ccsp"
TOKEN_URL = f"{BASE_URL}token"


def main():
    """
    Main function to run the Selenium automation.
    """
    # Initialize the Chrome WebDriver
    # Make sure you have chromedriver installed and in your PATH,
    # or specify the path to it.
    from selenium.webdriver.chrome.service import Service
    from webdriver_manager.chrome import ChromeDriverManager

    options = webdriver.ChromeOptions()
    options.add_argument(
        "user-agent=Mozilla/5.0 (Linux; Android 4.1.1; Galaxy Nexus Build/JRO03C) AppleWebKit/535.19 (KHTML, like Gecko) Chrome/18.0.1025.166 Mobile Safari/535.19_CCS_APP_AOS"
    )
    driver = webdriver.Chrome(
        service=Service(ChromeDriverManager().install()), options=options
    )
    driver.maximize_window()

    # 1. Open the login page
    print(f"Opening login page: {LOGIN_URL}")
    driver.get(LOGIN_URL)

    print("\n" + "=" * 50)
    print("Please log in manually in the browser window.")
    print("The script will wait for you to complete the login...")
    print("=" * 50 + "\n")

    try:
        wait = WebDriverWait(driver, 300)  # 300-second timeout
        wait.until(
            EC.presence_of_element_located((By.CSS_SELECTOR, SUCCESS_ELEMENT_SELECTOR))
        )
        print("✅ Login successful! Element found.")
        driver.get(REDIRECT_URL)

        print("Waiting for redirect with code...")
        try:
            # Wait up to 20 seconds for the URL to contain 'code='
            WebDriverWait(driver, 20).until(lambda d: "code=" in d.current_url)
        except TimeoutException:
            print(
                "⚠️ Timed out waiting for 'code=' in URL. Checking current URL anyway..."
            )

        current_url = driver.current_url
        print(f"DEBUG: Current URL after redirect: {current_url}")

        # Try a more generic regex for the code
        match = re.search(r"code=([^&]+)", current_url)
        if match:
            code = match.group(1)
            print(f"DEBUG: Extracted code: {code}")
        else:
            print("❌ Could not find 'code' parameter in URL")
            return

        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT_URL_FINAL,
            "client_id": CLIENT_ID,
            "client_secret": "secret",
        }
        response = session.post(TOKEN_URL, data=data)
        if response.status_code == 200:
            tokens = response.json()
            if tokens is not None:
                refresh_token = tokens["refresh_token"]
                access_token = tokens["access_token"]
                print(
                    f"\n✅ Your tokens are:\n\n- Refresh Token: {refresh_token}\n- Access Token: {access_token}"
                )
        else:
            print(f"\n❌ Error getting tokens from der API!\n{response.text}")

    except TimeoutException:
        print(
            "❌ Timed out after 5 minutes. Login was not completed or the success element was not found."
        )
    except Exception as e:
        print(f"An unexpected error occurred: {e}")
    finally:
        print("Cleaning up and closing the browser.")
        driver.quit()


if __name__ == "__main__":
    main()
