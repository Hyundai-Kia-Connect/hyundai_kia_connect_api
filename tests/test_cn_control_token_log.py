"""China commands must not log the control token issued for the PIN."""

from __future__ import annotations

import datetime as dt
import json
import logging
from collections.abc import Iterator
from typing import Any
from unittest.mock import patch

import pytest
import requests

from hyundai_kia_connect_api.const import BRAND_KIA, BRANDS, REGION_CHINA, REGIONS
from hyundai_kia_connect_api.Token import Token
from hyundai_kia_connect_api.Vehicle import Vehicle
from hyundai_kia_connect_api.VehicleManager import VehicleManager


@pytest.fixture(autouse=True)
def _fake_transport() -> Iterator[None]:
    def fake_request(
        self: requests.Session, method: str, url: str, **kwargs: Any
    ) -> requests.Response:
        if url.endswith("/user/pin"):
            body: dict[str, Any] = {
                "controlToken": "CONTROL-SECRET",
                "expiresTime": 600,
            }
        else:
            body = {"retCode": "S", "resCode": "0000", "msgId": "MSG-1"}
        response = requests.Response()
        response.status_code = 200
        response.url = url
        response._content = json.dumps(body).encode()
        return response

    with patch.object(requests.Session, "request", fake_request):
        yield


def test_stop_climate_does_not_log_control_token(
    caplog: pytest.LogCaptureFixture,
) -> None:
    api = VehicleManager.get_implementation_by_region_brand(
        next(k for k, v in REGIONS.items() if v == REGION_CHINA),
        next(k for k, v in BRANDS.items() if v == BRAND_KIA),
        "en",
    )
    token = Token(
        username="user",
        password="password",
        pin="1234",
        access_token="Bearer access",
        device_id="0123456789abcdef0123456789abcdef",
        valid_until=dt.datetime.now() + dt.timedelta(hours=1),
    )
    vehicle = Vehicle(id="VEHICLE-ID", VIN="VIN0000000000000", year=2024)

    with caplog.at_level(logging.DEBUG):
        api.stop_climate(token, vehicle)

    assert token.control_token == "Bearer CONTROL-SECRET"
    assert "CONTROL-SECRET" not in caplog.text
