from hyundai_kia_connect_api import VehicleManager
import os

from hyundai_kia_connect_api.const import Brand, Region

username = os.getenv("BLUELINK_BR_USERNAME")
password = os.getenv("BLUELINK_BR_PASSWORD")
pin = os.getenv("BLUELINK_BR_PIN")


def test_brazil_login_hyundai():
    vm = VehicleManager(
        region=Region.BRAZIL,
        brand=Brand.HYUNDAI,
        username=username,
        password=password,
        pin=pin,
    )

    vm.check_and_refresh_token()
    breakpoint()
