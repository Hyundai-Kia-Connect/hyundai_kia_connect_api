"""VehicleManager.initialize is callable again and emits a DeprecationWarning.

Upstream shipped `@DeprecationWarning` used as a decorator (5dca692f, #976),
which turned the method into a DeprecationWarning instance: every call
raised TypeError ('DeprecationWarning' object is not callable) instead of
deprecating anything.
"""

import datetime as dt

import pytest

from hyundai_kia_connect_api.Token import Token
from hyundai_kia_connect_api.VehicleManager import VehicleManager


def test_initialize_warns_and_completes():
    manager = VehicleManager(
        region=1,
        brand=2,
        username="user",
        password="pass",
        pin="1234",
        geocode_api_enable=False,
    )

    class FakeApi:
        def login(self, username, password, token=None, otp_handler=None, pin=None):
            return Token(
                username=username,
                password=password,
                valid_until=dt.datetime.now(dt.UTC),
            )

    manager.api = FakeApi()
    manager.initialize_vehicles = lambda: None

    with pytest.warns(DeprecationWarning, match="deprecated"):
        manager.initialize()

    assert manager.token.username == "user"
