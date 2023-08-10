"""
import os

from hyundai_kia_connect_api.VehicleManager import VehicleManager


def test_AU_login():
    username = os.environ["KIA_AU_USERNAME"]
    password = os.environ["KIA_AU_PASSWORD"]
    pin = os.environ["KIA_AU_PIN"]
    vm = VehicleManager(
        region=5,
        brand=2,
        username=username,
        password=password,
        pin=pin,
        geocode_api_enable=False,
    )
    vm.check_and_refresh_token()
    vm.check_and_force_update_vehicles(force_refresh_interval=600)
    print("Found: " + list(vm.vehicles.values())[0].name)
    assert len(vm.vehicles.keys()) > 0
"""
