from hyundai_kia_connect_api import VehicleManager
import os
import pytest
from typing import TYPE_CHECKING

from hyundai_kia_connect_api.const import Brand, Region

if TYPE_CHECKING:
    from hyundai_kia_connect_api.HyundaiBlueLinkApiBR import HyundaiBlueLinkApiBR

# To test this, create a .env file with the following variables:
#
# BLUELINK_BR_USERNAME=your_username
# BLUELINK_BR_PASSWORD=your_password
# BLUELINK_BR_PIN=your_pin
#
# pytest will automatically load the .env file.
#
# Then, run the tests with:
# pytest -m br

username = os.getenv("BLUELINK_BR_USERNAME")
password = os.getenv("BLUELINK_BR_PASSWORD")
pin = os.getenv("BLUELINK_BR_PIN")


@pytest.mark.br
class TestBrazilHyundaiAPI:
    @classmethod
    def setup_class(self):
        if not username or not password or not pin:
            raise ValueError(
                "BLUELINK_BR_USERNAME, BLUELINK_BR_PASSWORD, and BLUELINK_BR_PIN must be set to run this test file."
            )

        self.manager = VehicleManager(
            region=Region.BRAZIL,
            brand=Brand.HYUNDAI,
            username=username,
            password=password,
            pin=pin,
        )
        self.manager.check_and_refresh_token()
        self.api: "HyundaiBlueLinkApiBR" = (
            self.manager.get_implementation_by_region_brand(
                Region.BRAZIL, Brand.HYUNDAI, language="en-US"
            )
        )

    def test_login_update_hyundai(self):
        self.manager.check_and_refresh_token()
        self.manager.update_all_vehicles_with_cached_state()
        assert len(self.manager.vehicles.keys()) > 0

    def test_api_vehicle_get_location(self):
        token = self.manager.token
        vehicle = list(self.manager.vehicles.values())[0]
        location = self.api._get_vehicle_location(token, vehicle)
        assert "coords" in location
