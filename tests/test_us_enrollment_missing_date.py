"""Hyundai USA get_vehicles when an enrollment entry has no enrollmentDate.

Hyundai-Kia-Connect/kia_uvo#2128: the setup failed with KeyError:
'enrollmentDate'. The body is the real usa_hyundai capture with that key
removed from every vehicle.
"""

from __future__ import annotations

import datetime as dt
import json
from typing import Any
from unittest.mock import patch

import requests

from hyundai_kia_connect_api.const import BRAND_HYUNDAI, BRANDS, REGION_USA, REGIONS
from hyundai_kia_connect_api.Token import Token
from hyundai_kia_connect_api.VehicleManager import VehicleManager
from tests.fixture_helpers import FIXTURES_DIR


def test_get_vehicles_without_enrollment_date() -> None:
    body = json.loads((FIXTURES_DIR / "vehicle_lists" / "usa_hyundai.json").read_text())
    body.pop("_fixture_meta")
    for entry in body["enrolledVehicleDetails"]:
        del entry["vehicleDetails"]["enrollmentDate"]

    def fake_request(
        self: requests.Session, method: str, url: str, **kwargs: Any
    ) -> requests.Response:
        response = requests.Response()
        response.status_code = 200
        response.url = url
        response._content = json.dumps(body).encode()
        return response

    token = Token(
        username="user",
        password="password",
        pin="1234",
        access_token="Bearer access",
        refresh_token="refresh",
        device_id="0123456789abcdef0123456789abcdef",
        valid_until=dt.datetime.now() + dt.timedelta(hours=1),
    )
    api = VehicleManager.get_implementation_by_region_brand(
        next(k for k, v in REGIONS.items() if v == REGION_USA),
        next(k for k, v in BRANDS.items() if v == BRAND_HYUNDAI),
        "en",
    )
    with patch.object(requests.Session, "request", fake_request):
        vehicles = api.get_vehicles(token)

    assert len(vehicles) == len(body["enrolledVehicleDetails"])
    assert all(v.registration_date is None for v in vehicles)
