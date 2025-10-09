"""
import os

from hyundai_kia_connect_api.VehicleManager import VehicleManager


def test_BR_login():
    username = os.environ["HYUNDAI_BR_USERNAME"]
    password = os.environ["HYUNDAI_BR_PASSWORD"]
    pin = os.environ.get("HYUNDAI_BR_PIN", "")
    vm = VehicleManager(
        region=8,
        brand=2,
        username=username,
        password=password,
        pin=pin,
        geocode_api_enable=False,
    )
    vm.check_and_refresh_token()
    vm.check_and_force_update_vehicles(force_refresh_interval=600)
    if len(vm.vehicles.keys()) > 0:
        first_vehicle = list(vm.vehicles.values())[0]
        print(f"Found: {first_vehicle.name} (Model: {first_vehicle.model})")
    assert len(vm.vehicles.keys()) > 0
"""
