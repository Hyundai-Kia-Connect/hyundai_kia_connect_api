from hyundai_kia_connect_api import VehicleManager
import os
import pytest

from hyundai_kia_connect_api.const import Brand, Region

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


@pytest.fixture
def manager():
    return VehicleManager(
        region=Region.BRAZIL,
        brand=Brand.HYUNDAI,
        username=username,
        password=password,
        pin=pin,
    )


@pytest.mark.br
class TestBrazilHyundaiAPI:
    @classmethod
    def setup_class(self):
        if not username or not password or not pin:
            raise ValueError(
                "BLUELINK_BR_USERNAME, BLUELINK_BR_PASSWORD, and BLUELINK_BR_PIN must be set to run this test file."
            )

    def test_login_hyundai(self, manager):
        manager.check_and_refresh_token()
        manager.update_all_vehicles_with_cached_state()
        assert len(manager.vehicles.keys()) > 0
