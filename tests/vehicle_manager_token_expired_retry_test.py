"""VehicleManager refreshes and retries once when the server says the token
expired before Token.valid_until.

check_and_refresh_token() trusts valid_until (test_token() is a no-op for most
regions), so a server that expires tokens early left every poll failing with
"Key not authorized: token has expired" until valid_until finally passed.
"""

import datetime as dt

import pytest

from hyundai_kia_connect_api.ApiImpl import ApiImpl
from hyundai_kia_connect_api.ApiImplType1 import _check_response_for_errors
from hyundai_kia_connect_api.exceptions import AuthenticationError, TokenExpiredError
from hyundai_kia_connect_api.Token import Token
from hyundai_kia_connect_api.Vehicle import Vehicle
from hyundai_kia_connect_api.VehicleManager import VehicleManager


class EarlyExpiryApi(ApiImpl):
    """API whose data calls reject a token until it has been refreshed."""

    def __init__(self, failures: int = 1, error=None):
        super().__init__()
        self.failures = failures
        self.error = error or TokenExpiredError("Key not authorized: token has expired")
        self.refresh_count = 0
        self.tokens_seen: list[str] = []

    def refresh_access_token(self, token):
        self.refresh_count += 1
        return Token(
            username=token.username,
            password=token.password,
            access_token=f"Bearer refreshed-{self.refresh_count}",
            refresh_token=token.refresh_token,
            valid_until=dt.datetime.now(dt.UTC) + dt.timedelta(hours=1),
        )

    def _data_call(self, token):
        self.tokens_seen.append(token.access_token)
        if self.failures > 0:
            self.failures -= 1
            raise self.error

    def update_vehicle_with_cached_state(self, token, vehicle):
        self._data_call(token)

    def force_refresh_vehicle_state(self, token, vehicle):
        self._data_call(token)

    def refresh_vehicles(self, token, vehicles):
        return vehicles


def make_manager(monkeypatch, api) -> VehicleManager:
    monkeypatch.setattr(
        VehicleManager,
        "get_implementation_by_region_brand",
        lambda *args, **kwargs: api,
    )
    manager = VehicleManager(
        region=6,
        brand=1,
        username="user",
        password="RealPassword",
        pin="1234",
        geocode_api_enable=False,
    )
    # valid_until says the token is good for hours; the server disagrees.
    manager.token = Token(
        username="user",
        password="RealPassword",
        access_token="Bearer original",
        refresh_token="rtk",
        valid_until=dt.datetime.now(dt.UTC) + dt.timedelta(hours=22),
    )
    manager.vehicles = {"veh-1": Vehicle(id="veh-1")}
    return manager


@pytest.mark.parametrize(
    "message",
    ["Key not authorized: token has expired", "Key not authorized: Token is expired"],
)
def test_expired_token_messages_raise_token_expired_error(message):
    with pytest.raises(TokenExpiredError):
        _check_response_for_errors({"error": message})


def test_token_expired_error_is_an_authentication_error():
    # Existing `except AuthenticationError` handlers must keep catching it.
    assert issubclass(TokenExpiredError, AuthenticationError)


def test_cached_update_refreshes_and_retries_with_new_token(monkeypatch):
    api = EarlyExpiryApi(failures=1)
    manager = make_manager(monkeypatch, api)

    manager.update_vehicle_with_cached_state("veh-1")

    assert api.refresh_count == 1
    assert api.tokens_seen == ["Bearer original", "Bearer refreshed-1"]
    assert manager.token.access_token == "Bearer refreshed-1"


def test_force_refresh_refreshes_and_retries_with_new_token(monkeypatch):
    api = EarlyExpiryApi(failures=1)
    manager = make_manager(monkeypatch, api)

    manager.force_refresh_vehicle_state("veh-1")

    assert api.refresh_count == 1
    assert api.tokens_seen == ["Bearer original", "Bearer refreshed-1"]


def test_retries_only_once(monkeypatch):
    api = EarlyExpiryApi(failures=2)
    manager = make_manager(monkeypatch, api)

    with pytest.raises(TokenExpiredError):
        manager.update_vehicle_with_cached_state("veh-1")

    assert api.refresh_count == 1
    assert len(api.tokens_seen) == 2


def test_other_authentication_errors_are_not_retried(monkeypatch):
    api = EarlyExpiryApi(failures=1, error=AuthenticationError("Login Failed"))
    manager = make_manager(monkeypatch, api)

    with pytest.raises(AuthenticationError):
        manager.update_vehicle_with_cached_state("veh-1")

    assert api.refresh_count == 0
    assert api.tokens_seen == ["Bearer original"]


def test_no_refresh_when_call_succeeds(monkeypatch):
    api = EarlyExpiryApi(failures=0)
    manager = make_manager(monkeypatch, api)

    manager.update_vehicle_with_cached_state("veh-1")

    assert api.refresh_count == 0
    assert api.tokens_seen == ["Bearer original"]
