import os
import pytest
from hyundai_kia_connect_api.VehicleManager import VehicleManager


def test_AU_login():
    username = os.getenv("KIA_AU_USERNAME")
    password = os.getenv("KIA_AU_PASSWORD")
    pin = os.getenv("KIA_AU_PIN")

    if not any([username, password, pin]):
        pytest.skip(
            "KIA_AU_USERNAME, KIA_AU_PASSWORD, and KIA_AU_PIN must be set to run this test."
        )

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
