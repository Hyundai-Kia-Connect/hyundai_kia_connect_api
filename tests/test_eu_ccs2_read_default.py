"""Behavior tests for the EU read switch to /ccs2/carstatus/latest.

The vendor app reads cached status from the CCS2 endpoint for every car
(issue #1546); the per-flag endpoint switch and the legacy /status/latest
read are gone. The updates snapshot scenario pins the parsed vehicle; these
tests pin the call order, the per-flag protocol header and the propagation.
"""

from __future__ import annotations

import datetime as dt
import json
from typing import Any, cast
from unittest.mock import patch
from urllib.parse import urlparse

import pytest
import requests

from hyundai_kia_connect_api.const import BRANDS, ENGINE_TYPES, REGIONS
from hyundai_kia_connect_api.exceptions import (
    RequestTimeoutError,
    ServiceTemporaryUnavailable,
)
from hyundai_kia_connect_api.Token import Token
from hyundai_kia_connect_api.Vehicle import Vehicle
from hyundai_kia_connect_api.VehicleManager import VehicleManager
from tests.fixture_helpers import FIXTURES_DIR
from tests.test_vehicle_update_snapshots import _resolve

FIFTY31 = {
    "retCode": "F",
    "resCode": "5031",
    "resMsg": "Unavailable remote control - Service Temporary Unavailable",
    "msgId": "MSG-1",
}

TIMEOUT = {
    "retCode": "F",
    "resCode": "9999",
    "resMsg": "Undefined Error - Response timeout",
}


def _manager(ccs2_support: int | None) -> tuple[VehicleManager, Vehicle]:
    manager = VehicleManager(
        region=next(k for k, v in REGIONS.items() if v == "Europe"),
        brand=next(k for k, v in BRANDS.items() if v == "Kia"),
        username="user",
        password="password",
        pin="1234",
        token=Token(
            username="user",
            password="password",
            pin="1234",
            access_token="Bearer access",
            refresh_token="refresh",
            device_id="0123456789abcdef0123456789abcdef",
            valid_until=dt.datetime.now(dt.UTC) + dt.timedelta(hours=1),
        ),
    )
    vehicle = Vehicle(
        id="VEHICLE-1",
        VIN="TESTVIN0000000001",
        engine_type=ENGINE_TYPES.PHEV,
        ccu_ccs2_protocol_support=ccs2_support,
    )
    manager.vehicles = {vehicle.id: vehicle}
    return manager, vehicle


def _run(
    responses: dict[str, Any], ccs2_support: int | None
) -> tuple[list[dict[str, Any]], Vehicle]:
    """Answer SPA requests by method + path suffix, like the snapshot harness."""
    pending = {
        key: list(value) if isinstance(value, list) else [value]
        for key, value in responses.items()
    }
    sent: list[dict[str, Any]] = []

    def fake_request(
        self: requests.Session, method: str, url: str, **kwargs: Any
    ) -> requests.Response:
        path = urlparse(url).path
        sent.append(
            {"method": method.upper(), "url": url, "headers": kwargs.get("headers")}
        )
        matches = [
            k
            for k in pending
            if k.split(" ", 1)[0] == method.upper()
            and path.endswith(k.split(" ", 1)[1])
        ]
        assert matches, f"unexpected request {method.upper()} {path}"
        assert pending[matches[0]], f"no response left for {matches[0]}"
        answer = pending[matches[0]].pop(0)
        response = requests.Response()
        response.status_code = answer.get("status", 200)
        body = _resolve(answer.get("body"))
        response._content = b"" if body is None else json.dumps(body).encode()
        return response

    manager, vehicle = _manager(ccs2_support)
    with patch.object(requests.Session, "request", fake_request):
        manager.update_vehicle_with_cached_state(vehicle.id)
    return sent, vehicle


def _responses() -> dict[str, Any]:
    spec = json.loads(
        (FIXTURES_DIR / "updates" / "eu_kia_e_niro_2019_issue_867.json").read_text()
    )
    return cast(dict[str, Any], _resolve(spec["responses"]))


@pytest.mark.parametrize("ccs2_support", [0, 1, None])
def test_cached_read_hits_ccs2_endpoint_once(ccs2_support: int | None) -> None:
    sent, vehicle = _run(_responses(), ccs2_support)

    requests_seen = [f"{r['method']} {urlparse(r['url']).path}" for r in sent]
    assert requests_seen == [
        "GET /api/v1/spa/vehicles/VEHICLE-1/ccs2/carstatus/latest",
        "GET /api/v1/spa/vehicles/VEHICLE-1/location/park",
        "POST /api/v1/spa/vehicles/VEHICLE-1/drvhistory",
        "POST /api/v1/spa/vehicles/VEHICLE-1/drvhistory",
    ]
    # The protocol header follows the vehicle flag; None parses as 0.
    assert sent[0]["headers"]["Ccuccs2protocolsupport"] == str(ccs2_support or 0)
    assert vehicle.ccu_ccs2_protocol_support == ccs2_support


def test_read_propagates_when_ccs2_unavailable() -> None:
    responses = _responses()
    responses["GET /vehicles/VEHICLE-1/ccs2/carstatus/latest"] = {"body": FIFTY31}

    with pytest.raises(ServiceTemporaryUnavailable):
        _run(responses, 0)


def test_read_propagates_on_timeout() -> None:
    responses = _responses()
    responses["GET /vehicles/VEHICLE-1/ccs2/carstatus/latest"] = {"body": TIMEOUT}

    with pytest.raises(RequestTimeoutError):
        _run(responses, 0)
