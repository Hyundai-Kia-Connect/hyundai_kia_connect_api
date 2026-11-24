"""Hyundai USA commands must not log the access token or PIN sent in headers."""

from __future__ import annotations

import datetime as dt
import json
import logging
from collections.abc import Callable, Iterator
from typing import Any
from unittest.mock import patch

import pytest
import requests

from hyundai_kia_connect_api.ApiImpl import ClimateRequestOptions
from hyundai_kia_connect_api.const import (
    BRAND_HYUNDAI,
    BRANDS,
    ENGINE_TYPES,
    REGION_USA,
    REGIONS,
)
from hyundai_kia_connect_api.HyundaiBlueLinkApiUSA import HyundaiBlueLinkApiUSA
from hyundai_kia_connect_api.Token import Token
from hyundai_kia_connect_api.Vehicle import Vehicle
from hyundai_kia_connect_api.VehicleManager import VehicleManager

_SECRETS = ("ACCESS-SECRET", "PIN-SECRET")

COMMANDS: dict[str, Callable[[HyundaiBlueLinkApiUSA, Token, Vehicle], Any]] = {
    "start_climate": lambda api, t, v: api.start_climate(
        t, v, ClimateRequestOptions(set_temp=72, duration=10, climate=True)
    ),
    "stop_climate": lambda api, t, v: api.stop_climate(t, v),
    "start_charge": lambda api, t, v: api.start_charge(t, v),
    "stop_charge": lambda api, t, v: api.stop_charge(t, v),
    "set_charge_limits": lambda api, t, v: api.set_charge_limits(t, v, 80, 90),
}


@pytest.fixture(autouse=True)
def _fake_transport() -> Iterator[None]:
    def fake_request(
        self: requests.Session, method: str, url: str, **kwargs: Any
    ) -> requests.Response:
        response = requests.Response()
        response.status_code = 200
        response.url = url
        response.headers.update({"tmsTid": "TX-1"})
        response._content = json.dumps({}).encode()
        return response

    with patch.object(requests.Session, "request", fake_request):
        yield


@pytest.mark.parametrize("command", COMMANDS)
def test_command_does_not_log_credentials(
    command: str, caplog: pytest.LogCaptureFixture
) -> None:
    api = VehicleManager.get_implementation_by_region_brand(
        next(k for k, v in REGIONS.items() if v == REGION_USA),
        next(k for k, v in BRANDS.items() if v == BRAND_HYUNDAI),
        "en",
    )
    assert isinstance(api, HyundaiBlueLinkApiUSA)
    token = Token(
        username="user",
        password="password",
        pin="PIN-SECRET",
        access_token="ACCESS-SECRET",
        valid_until=dt.datetime.now() + dt.timedelta(hours=1),
    )
    vehicle = Vehicle(
        id="VEHICLE-ID",
        VIN="VIN0000000000000",
        year=2024,
        engine_type=ENGINE_TYPES.EV,
    )

    with caplog.at_level(logging.DEBUG):
        COMMANDS[command](api, token, vehicle)

    for secret in _SECRETS:
        assert secret not in caplog.text
