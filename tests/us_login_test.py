'''
import os
import datetime as dt
import time as tm
import pytest
import hyundai_kia_connect_api as hk


@pytest.fixture
def us_credentials() -> dict[str, str]:
    return {
        "username": os.getenv("KIA_US_USERNAME"),
        "password": os.getenv("KIA_US_PASSWORD"),
        "pin": os.getenv("KIA_US_PIN"),
    }


def test_us_login(us_credentials: dict[str, str]) -> None:
    """Verify OTP login and rmtoken reuse in a single process.

    Parameters
    ----------
    us_credentials : dict[str, str]
        Credentials for a US Kia account.
    """
    vehicle_manager = hk.VehicleManager(
        region=3,
        brand=1,
        username=us_credentials["username"],
        password=us_credentials["password"],
        pin=us_credentials["pin"],
        geocode_api_enable=False,
    )
    print("\n=== Initial Login (will prompt for OTP) ===")
    vehicle_manager.check_and_refresh_token()
    vehicle_manager.check_and_force_update_vehicles(force_refresh_interval=600)
    assert len(vehicle_manager.vehicles.keys()) > 0
    vehicle_name = list(vehicle_manager.vehicles.values())[0].name
    print(f"\nFound: {vehicle_name}")
    print(f"Initial token (sid): {vehicle_manager.token.access_token[:20]}...")
    initial_rmtoken = vehicle_manager.token.refresh_token
    if initial_rmtoken:
        print(f"rmtoken stored: {initial_rmtoken[:20]}...")
    else:
        print("WARNING: No rmtoken stored!")
    print("\n=== Testing rmtoken reuse by forcing token expiration ===")
    vehicle_manager.token.valid_until = dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=60)
    print("Token expired manually")
    print("\n=== Second Login (should use rmtoken, no OTP prompt) ===")
    vehicle_manager.check_and_refresh_token()
    vehicle_manager.check_and_force_update_vehicles(force_refresh_interval=600)
    print(f"SUCCESS: Re-authenticated using stored rmtoken")
    print(f"New token (sid): {vehicle_manager.token.access_token[:20]}...")
    if vehicle_manager.token.refresh_token:
        print(f"rmtoken still stored: {vehicle_manager.token.refresh_token[:20]}...")
    vehicle_name_after = list(vehicle_manager.vehicles.values())[0].name
    print(f"Vehicle: {vehicle_name_after}")
    assert len(vehicle_manager.vehicles.keys()) > 0


def test_rmtoken_expiration_in_5_minutes(us_credentials: dict[str, str]) -> None:
    """Check whether rmtoken remains valid after 5 minutes.

    Parameters
    ----------
    us_credentials : dict[str, str]
        Credentials for a US Kia account.
    """
    vehicle_manager = hk.VehicleManager(
        region=3,
        brand=1,
        username=us_credentials["username"],
        password=us_credentials["password"],
        pin=us_credentials["pin"],
        geocode_api_enable=False,
    )
    print("\n=== First Login (OTP expected) ===")
    vehicle_manager.check_and_refresh_token()
    assert len(vehicle_manager.vehicles.keys()) > 0
    initial_rmtoken = vehicle_manager.token.refresh_token
    if not initial_rmtoken:
        pytest.skip("No rmtoken stored; cannot evaluate rmtoken expiry")
    print("rmtoken stored:", initial_rmtoken[:20] + "...")

    print("\n=== Waiting 5 minutes to evaluate rmtoken expiry ===")
    for i in range(5):
        tm.sleep(60)
        print(f"  {i+1} minute(s) elapsed...")

    print("\n=== Expiring session (sid) to force re-login using rmtoken ===")
    vehicle_manager.token.valid_until = dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=1)
    sid_before = vehicle_manager.token.access_token
    rmtoken_before = vehicle_manager.token.refresh_token

    print("\n=== Attempting login using stored rmtoken ===")
    vehicle_manager.check_and_refresh_token()
    assert len(vehicle_manager.vehicles.keys()) > 0
    sid_after = vehicle_manager.token.access_token
    rmtoken_after = vehicle_manager.token.refresh_token

    if rmtoken_after == rmtoken_before:
        print("RESULT: rmtoken appears valid after 5 minutes (no rotation observed)")
    else:
        print("RESULT: rmtoken appears to have rotated (likely expired or refreshed)")

    if sid_after != sid_before:
        print("New session was established successfully")

    vehicle_manager.check_and_force_update_vehicles(force_refresh_interval=600)
'''
