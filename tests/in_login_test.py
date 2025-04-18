import os

from hyundai_kia_connect_api.VehicleManager import VehicleManager


def test_IN_login():
    username = os.environ["KIA_IN_NISHANT_USERNAME"]
    password = os.environ["KIA_IN_NISHANT_PASSWORD"]
    pin = os.environ["KIA_IN_NISHANT_PIN"]
    vm = VehicleManager(
        region=6, brand=2, username=username, password=password, pin=pin
    )
    vm.check_and_refresh_token()
    vm.update_all_vehicles_with_cached_state()
    assert len(vm.vehicles.keys()) > 0
