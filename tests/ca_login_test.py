import os

from hyundai_kia_connect_api.VehicleManager import VehicleManager


def test_CA_login():
    username = os.environ["KIA_CA_CDNNINJA_USERNAME"]
    password = os.environ["KIA_CA_CDNNINJA_PASSWORD"]
    pin = os.environ["KIA_CA_CDNNINJA_PIN"]
    vm = VehicleManager(
        region=2,
        brand=1,
        username=username,
        password=password,
        pin=pin,
        geocode_api_enable=True,
    )
    vm.check_and_refresh_token()
    assert len(vm.vehicles.keys()) > 0
