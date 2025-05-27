import os
import pytest
from hyundai_kia_connect_api.VehicleManager import VehicleManager


def test_CA_login():
    username = os.getenv("KIA_CA_CDNNINJA_USERNAME")
    password = os.getenv("KIA_CA_CDNNINJA_PASSWORD")
    pin = os.getenv("KIA_CA_CDNNINJA_PIN")

    if not any([username, password, pin]):
        pytest.skip(
            "KIA_CA_CDNNINJA_USERNAME, KIA_CA_CDNNINJA_PASSWORD, and KIA_CA_CDNNINJA_PIN must be set to run this test."
        )

    vm = VehicleManager(
        region=2,
        brand=1,
        username=username,
        password=password,
        pin=pin,
        geocode_api_enable=True,
    )
    vm.check_and_refresh_token()
    vm.check_and_force_update_vehicles(force_refresh_interval=600)
    assert len(vm.vehicles.keys()) > 0
