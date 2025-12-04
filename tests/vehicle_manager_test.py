import datetime as dt

from hyundai_kia_connect_api.ApiImpl import ApiImpl
from hyundai_kia_connect_api.Token import Token
from hyundai_kia_connect_api.VehicleManager import VehicleManager


class DummyApi(ApiImpl):
    def __init__(self):
        super().__init__()
        self.login_calls = 0

    def login(self, username, password, token=None, otp_handler=None):
        self.login_calls += 1
        return Token(
            username=username,
            password=password,
            valid_until=dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=1),
        )

    def get_vehicles(self, token):
        return []

    def refresh_vehicles(self, token, vehicles):
        return vehicles


def test_check_and_refresh_token_handles_min_datetime(monkeypatch):
    dummy_api = DummyApi()
    monkeypatch.setattr(
        VehicleManager,
        "get_implementation_by_region_brand",
        lambda *args, **kwargs: dummy_api,
    )
    manager = VehicleManager(
        region=3,
        brand=1,
        username="user",
        password="pass",
        pin="1234",
        geocode_api_enable=False,
    )
    manager.token = Token(valid_until=dt.datetime.min)
    assert manager.check_and_refresh_token() is True
    assert dummy_api.login_calls == 1
