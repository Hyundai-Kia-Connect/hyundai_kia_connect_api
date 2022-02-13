import os

from hyundai_kia_connect_api.VehicleManager import VehicleManager


def test_login():
    username = os.environ["KIA_EU_FUATAKGUN_USERNAME"]
    password = os.environ["KIA_EU_FUATAKGUN_PASSWORD"]
    pin = ""
    vm = VehicleManager(
        region=1, brand=1, username=username, password=password, pin=pin
    )
    vm.check_and_refresh_token()
    assert len(vm.vehicles.keys()) > 0
