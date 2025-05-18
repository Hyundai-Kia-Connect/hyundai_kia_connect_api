from hyundai_kia_connect_api import VehicleManager
import os

username = os.environ["BLUELINK_BR_USERNAME"]
password = os.environ["BLUELINK_BR_PASSWORD"]

def test_brazil_login_hyundai():
    vm = VehicleManager(
        region=8,  # 8 = Brazil
        brand=2,   # 2 = Hyundai
        username=username,
        password=password,
    )

    vm.check_and_refresh_token()
    vm.update_all_vehicles_with_cached_state()

    print(vm.vehicles)