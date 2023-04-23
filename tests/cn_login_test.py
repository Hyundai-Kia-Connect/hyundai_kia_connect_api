import os
import logging 
from hyundai_kia_connect_api.VehicleManager import VehicleManager

logging.basicConfig(level=logging.DEBUG)

def test_CN_login():
    username = os.environ["HYUNDAI_CN_USERNAME"]
    password = os.environ["HYUNDAI_CN_PASSWORD"]
    pin = os.environ["HYUNDAI_CN_PIN"]
    vm = VehicleManager(
        region=4, brand=2, username=username, password=password, pin=pin
    )
    vm.check_and_refresh_token()
    logging.debug('This is a debug information')
    vm.update_all_vehicles_with_cached_state()
    assert len(vm.vehicles.keys()) > 0
