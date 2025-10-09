"""
Brazilian Hyundai BlueLink API Login Test

This test validates the Brazilian API implementation (region 8).
It requires environment variables to be set with valid credentials.

Environment Variables:
- HYUNDAI_BR_USERNAME: Brazilian Hyundai BlueLink username/email
- HYUNDAI_BR_PASSWORD: Brazilian Hyundai BlueLink password
- HYUNDAI_BR_PIN: PIN (optional for Brazil, use empty string if not needed)

Test Vehicle: Hyundai Creta 2026 (ICE)
"""

import os

from hyundai_kia_connect_api.VehicleManager import VehicleManager


def test_BR_login():
    """Test login and vehicle retrieval for Brazilian Hyundai BlueLink API."""
    username = os.environ["HYUNDAI_BR_USERNAME"]
    password = os.environ["HYUNDAI_BR_PASSWORD"]
    pin = os.environ.get("HYUNDAI_BR_PIN", "")  # PIN is optional for Brazil

    # Initialize VehicleManager for Brazil (region 8) with Hyundai (brand 2)
    vm = VehicleManager(
        region=8,
        brand=2,
        username=username,
        password=password,
        pin=pin,
        geocode_api_enable=False,
    )

    # Check and refresh token
    vm.check_and_refresh_token()

    # Update all vehicles with cached state
    vm.check_and_force_update_vehicles(force_refresh_interval=600)

    # Print first vehicle name for debugging
    if len(vm.vehicles.keys()) > 0:
        first_vehicle = list(vm.vehicles.values())[0]
        print(f"Found: {first_vehicle.name} (Model: {first_vehicle.model})")

    # Assert at least one vehicle was found
    assert len(vm.vehicles.keys()) > 0, "No vehicles found for Brazilian account"
