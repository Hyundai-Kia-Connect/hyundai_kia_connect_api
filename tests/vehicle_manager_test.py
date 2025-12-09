import datetime as dt

import pytest  # type: ignore[import]

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


class DummyPersistedTokenApi(ApiImpl):
    def __init__(self, required_secret: str):
        super().__init__()
        self.required_secret = required_secret
        self.login_calls = 0

    def login(self, username, password, token=None, otp_handler=None) -> Token:
        self.login_calls += 1
        assert (
            password == self.required_secret
        ), "login() should receive the persisted refresh token when password is missing"
        return Token(
            username=username,
            password=password,
            refresh_token=password,
            access_token="new-access-token",
            valid_until=dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=5),
        )

    def get_vehicles(self, token):
        return []

    def refresh_vehicles(self, token, vehicles):
        return vehicles


def test_initialize_reuses_seeded_refresh_token_when_password_missing(monkeypatch):
    required_secret = "A" * 48
    dummy_api = DummyPersistedTokenApi(required_secret)
    monkeypatch.setattr(
        VehicleManager,
        "get_implementation_by_region_brand",
        lambda *args, **kwargs: dummy_api,
    )
    manager = VehicleManager(
        region=3,
        brand=1,
        username="user",
        password=None,
        pin="1234",
        geocode_api_enable=False,
    )
    manager.token = Token(
        username="user",
        refresh_token=required_secret,
        valid_until=dt.datetime.min.replace(tzinfo=dt.timezone.utc),
    )

    manager.initialize()
    assert dummy_api.login_calls == 1


def test_check_and_refresh_token_reuses_seeded_refresh_token(monkeypatch):
    required_secret = "B" * 48
    dummy_api = DummyPersistedTokenApi(required_secret)
    monkeypatch.setattr(
        VehicleManager,
        "get_implementation_by_region_brand",
        lambda *args, **kwargs: dummy_api,
    )
    manager = VehicleManager(
        region=3,
        brand=1,
        username="user",
        password=None,
        pin="1234",
        geocode_api_enable=False,
    )
    manager.token = Token(
        username="user",
        refresh_token=required_secret,
        valid_until=dt.datetime.min.replace(tzinfo=dt.timezone.utc),
    )

    assert manager.check_and_refresh_token() is True
    assert dummy_api.login_calls == 1
