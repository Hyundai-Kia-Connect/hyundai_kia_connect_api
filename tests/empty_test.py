import os

from hyundai_kia_connect_api.VehicleManager import VehicleManager


def login():
    username = os.environ["KIA_CA_CDNNINJA_USERNAME"]
    password = os.environ["KIA_CA_CDNNINJA_PASSWORD"]
    pin = os.environ["KIA_CA_CDNNINJA_PIN"]
    vm = VehicleManager(
        region=1, brand=2, username=username, password=password, pin=pin
    )
    print(vm.check_and_refresh_token())
    print(vm.vehicles)
    return True
