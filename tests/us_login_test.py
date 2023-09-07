import os

from hyundai_kia_connect_api.VehicleManager import VehicleManager


def test_US_login():
    username = os.environ["HYUNDAI_USERNAME"]
    password = os.environ["HYUNDAI_PWD"]
    pin = os.environ["HYUNDAI_PIN"]
    vm = VehicleManager(
        region=3, brand=2, username=username, password=password, pin=pin
    )

    vm.check_and_refresh_token()
    vm.update_all_vehicles_with_cached_state()
    vehicle_id = next(iter(vm.vehicles))
    # test_start_engine(vm, vehicle_id)
    # test_stop_engine(vm, vehicle_id)

def test_start_engine(vm: VehicleManager, vehicle_id: str):
    vm.start_engine(vehicle_id, None)

def test_stop_engine(vm: VehicleManager, vehicle_id: str):
    vm.stop_engine(vehicle_id)

test_US_login()
