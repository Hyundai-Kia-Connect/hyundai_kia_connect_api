import os
import pytest
from hyundai_kia_connect_api.VehicleManager import VehicleManager


@pytest.mark.enable_socket
def test_EU_login():
    username = os.getenv("KIA_EU_FUATAKGUN_USERNAME")
    password = os.getenv("KIA_EU_FUATAKGUN_PASSWORD")
    pin = os.getenv("KIA_EU_FUATAKGUN_PIN")

    if not any([username, password, pin]):
        pytest.skip(
            "KIA_EU_FUATAKGUN_USERNAME, KIA_EU_FUATAKGUN_PASSWORD, and KIA_EU_FUATAKGUN_PIN must be set to run this test."
        )

    vm = VehicleManager(
        region=1, brand=1, username=username, password=password, pin=pin
    )
    vm.check_and_refresh_token()
    vm.update_all_vehicles_with_cached_state()
    assert len(vm.vehicles.keys()) > 0
