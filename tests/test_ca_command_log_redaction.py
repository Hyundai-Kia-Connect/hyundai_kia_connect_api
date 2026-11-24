"""Canada commands must not log the PIN or the pAuth token it is exchanged for."""

from __future__ import annotations

import datetime as dt
import json
import logging
from collections.abc import Callable, Iterator
from typing import Any
from unittest.mock import patch

import pytest
import requests

from hyundai_kia_connect_api.ApiImpl import ApiImpl, ClimateRequestOptions
from hyundai_kia_connect_api.const import (
    BRAND_HYUNDAI,
    BRAND_KIA,
    BRANDS,
    ENGINE_TYPES,
    REGION_CANADA,
    REGIONS,
    VEHICLE_LOCK_ACTION,
)
from hyundai_kia_connect_api.Token import Token
from hyundai_kia_connect_api.Vehicle import Vehicle
from hyundai_kia_connect_api.VehicleManager import VehicleManager

_SECRETS = ("ACCESS-SECRET", "PIN-SECRET", "PAUTH-SECRET")

COMMANDS: dict[str, Callable[[ApiImpl, Token, Vehicle], Any]] = {
    "lock": lambda api, t, v: api.lock_action(t, v, VEHICLE_LOCK_ACTION.LOCK),
    "start_climate": lambda api, t, v: api.start_climate(
        t,
        v,
        ClimateRequestOptions(
            set_temp=21, duration=10, defrost=False, climate=True, heating=0
        ),
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
        body: dict[str, Any] = {"responseHeader": {"responseCode": 0}}
        if url.endswith("vrfypin"):
            body["result"] = {"pAuth": "PAUTH-SECRET"}
        response = requests.Response()
        response.status_code = 200
        response.url = url
        response.headers.update({"transactionId": "TX-1"})
        response._content = json.dumps(body).encode()
        return response

    with patch.object(requests.Session, "request", fake_request):
        yield


@pytest.mark.parametrize("command", COMMANDS)
@pytest.mark.parametrize("brand", [BRAND_KIA, BRAND_HYUNDAI])
def test_command_does_not_log_credentials(
    brand: str, command: str, caplog: pytest.LogCaptureFixture
) -> None:
    api = VehicleManager.get_implementation_by_region_brand(
        next(k for k, v in REGIONS.items() if v == REGION_CANADA),
        next(k for k, v in BRANDS.items() if v == brand),
        "en",
    )
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
