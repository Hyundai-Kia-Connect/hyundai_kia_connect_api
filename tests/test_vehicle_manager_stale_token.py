"""Stale refresh-token wedge: recovery via the manager's original credentials.

kia_uvo #1888: when Token.password holds the legacy 48-char refresh token and
refresh fails, the fallback full login re-enters the refresh-token grant with
the same stale value and wedges the account forever. The manager must retry
once with the credentials it was constructed with.
"""

import datetime as dt
import re

import pytest

from hyundai_kia_connect_api.ApiImpl import ApiImpl
from hyundai_kia_connect_api.exceptions import AuthenticationError
from hyundai_kia_connect_api.Token import Token
from hyundai_kia_connect_api.VehicleManager import VehicleManager

LEGACY_TOKEN = "A" * 48


class WedgedApi(ApiImpl):
    """API that fails refresh and logs every login attempt's password."""

    def __init__(self):
        super().__init__()
        self.login_passwords: list[str] = []

    def login(self, username, password, token=None, otp_handler=None, pin=None):
        self.login_passwords.append(password)
        if re.fullmatch(r"[A-Z0-9]{48}", password or ""):
            raise AuthenticationError("Received unexpected statusCode")
        return Token(
            username=username,
            password=password,
            valid_until=dt.datetime.now(dt.UTC) + dt.timedelta(minutes=30),
        )

    def refresh_access_token(self, token):
        raise AuthenticationError("Received unexpected statusCode")

    def get_vehicles(self, token):
        return []

    def refresh_vehicles(self, token, vehicles):
        return vehicles


def make_manager(api, password):
    manager = VehicleManager(
        region=1,
        brand=2,
        username="user",
        password=password,
        pin="1234",
        geocode_api_enable=False,
    )
    manager.token = Token(password=LEGACY_TOKEN, valid_until=dt.datetime.min)
    return manager


@pytest.fixture(name="api")
def api_fixture(monkeypatch):
    api = WedgedApi()
    monkeypatch.setattr(
        VehicleManager,
        "get_implementation_by_region_brand",
        lambda *args, **kwargs: api,
    )
    return api


def test_wedged_token_retries_with_original_password(api):
    manager = make_manager(api, password="RealPassword")
    assert manager.check_and_refresh_token() is True
    assert api.login_passwords == ["RealPassword"]


def test_no_retry_when_token_password_is_the_real_password(api):
    manager = make_manager(api, password=LEGACY_TOKEN)
    manager.token.password = LEGACY_TOKEN
    with pytest.raises(AuthenticationError):
        manager.check_and_refresh_token()
    assert api.login_passwords == []


def test_no_retry_when_manager_password_is_also_a_refresh_token(api):
    manager = make_manager(api, password=LEGACY_TOKEN)
    manager.token.password = LEGACY_TOKEN
    with pytest.raises(AuthenticationError):
        manager.check_and_refresh_token()
    assert api.login_passwords == []


def test_plain_password_failure_does_not_double_hit(api):
    """A non-wedge auth failure must not produce a second credential POST."""
    manager = make_manager(api, password="RealPassword")
    manager.token = Token(password="RealPassword", valid_until=dt.datetime.min)
    with pytest.raises(AuthenticationError):
        manager.check_and_refresh_token()
    assert api.login_passwords == []
