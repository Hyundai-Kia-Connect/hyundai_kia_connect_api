"""Cross-region snapshot tests for get_vehicles and refresh_vehicles.

Each implementation with a real vehicle-list capture in
``tests/fixtures/vehicle_lists`` reads it through a fake HTTP transport. The requests
sent and the fields each parsed Vehicle sets are compared to a syrupy
snapshot. Fields left at their Vehicle() default are omitted.

To update snapshots after an intentional change::

    pytest tests/test_vehicle_list_snapshots.py --snapshot-update
"""

from __future__ import annotations

import datetime as dt
import json
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import pytest
import requests

from hyundai_kia_connect_api.const import (
    BRAND_HYUNDAI,
    BRAND_KIA,
    BRANDS,
    REGION_CANADA,
    REGION_CHINA,
    REGION_EUROPE,
    REGION_USA,
    REGIONS,
)
from hyundai_kia_connect_api.Token import Token
from hyundai_kia_connect_api.Vehicle import Vehicle
from hyundai_kia_connect_api.VehicleManager import VehicleManager
from tests.fixture_helpers import FIXTURES_DIR
from tests.vehicle_snapshot_serializer import vehicle_to_dict

if TYPE_CHECKING:
    from syrupy.assertion import SnapshotAssertion

VEHICLE_LISTS_DIR = FIXTURES_DIR / "vehicle_lists"

# (region, brand) per implementation with a real vehicle-list capture; the
# fixture is vehicle_lists/<key>.json. BR and the EU CCI (GSPA) classes never
# log this response, so no capture exists for them.
IMPLEMENTATIONS = {
    "eu_hyundai": (REGION_EUROPE, BRAND_HYUNDAI),
    "ca_kia": (REGION_CANADA, BRAND_KIA),
    "ca_hyundai": (REGION_CANADA, BRAND_HYUNDAI),
    "usa_kia": (REGION_USA, BRAND_KIA),
    "usa_hyundai": (REGION_USA, BRAND_HYUNDAI),
    "cn_kia": (REGION_CHINA, BRAND_KIA),
}

_DEFAULTS = vehicle_to_dict(Vehicle())


def _key(mapping: dict[int, str], value: str) -> int:
    return next(k for k, v in mapping.items() if v == value)


def _set_fields(vehicle: Vehicle) -> dict[str, Any]:
    return {
        name: value
        for name, value in vehicle_to_dict(vehicle).items()
        if value != _DEFAULTS[name]
    }


def test_every_fixture_has_an_implementation() -> None:
    assert sorted(p.stem for p in VEHICLE_LISTS_DIR.glob("*.json")) == sorted(
        IMPLEMENTATIONS
    )


@pytest.mark.parametrize("impl", IMPLEMENTATIONS)
def test_vehicle_list(impl: str, snapshot: SnapshotAssertion) -> None:
    body = json.loads((VEHICLE_LISTS_DIR / f"{impl}.json").read_text())
    body.pop("_fixture_meta")
    sent: list[dict[str, str]] = []

    def fake_request(
        self: requests.Session, method: str, url: str, **kwargs: Any
    ) -> requests.Response:
        sent.append({"method": method.upper(), "url": url})
        response = requests.Response()
        response.status_code = 200
        response.url = url
        response._content = json.dumps(body).encode()
        return response

    region, brand = IMPLEMENTATIONS[impl]
    api = VehicleManager.get_implementation_by_region_brand(
        _key(REGIONS, region), _key(BRANDS, brand), "en"
    )
    token = Token(
        username="user",
        password="password",
        pin="1234",
        access_token="Bearer access",
        refresh_token="refresh",
        device_id="0123456789abcdef0123456789abcdef",
        valid_until=dt.datetime.now() + dt.timedelta(hours=1),
    )

    with patch.object(requests.Session, "request", fake_request):
        vehicles = {v.id: v for v in api.get_vehicles(token)}
        get_requests = list(sent)
        sent.clear()
        api.refresh_vehicles(token, vehicles)

    assert {
        "get_vehicles": get_requests,
        "refresh_vehicles": sent,
        "vehicles": {vid: _set_fields(v) for vid, v in vehicles.items()},
    } == snapshot
