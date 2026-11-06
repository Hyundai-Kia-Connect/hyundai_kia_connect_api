"""Behavior tests for the EU flag-0 5031 -> CCS2 cached-status fallback.

The /status/latest endpoint permanently returns resCode 5031 for some flag-0
cars (issue #1546, Kia Niro (PE) 2025); the CCS2 endpoint serves their cached
status. The updates snapshot scenario pins the parsed vehicle; these tests pin
the call order, the per-endpoint protocol header and the failure paths.
"""

from __future__ import annotations

import datetime as dt
import json
from typing import Any
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

SCENARIO = "eu_kia_niro_2025_flag0_fallback.json"

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


def _manager() -> tuple[VehicleManager, Vehicle]:
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
        ccu_ccs2_protocol_support=0,
    )
    manager.vehicles = {vehicle.id: vehicle}
    return manager, vehicle


def _run(
    responses: dict[str, Any],
) -> tuple[list[dict[str, Any]], VehicleManager, Vehicle]:
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

    manager, vehicle = _manager()
    with patch.object(requests.Session, "request", fake_request):
        manager.update_vehicle_with_cached_state(vehicle.id)
    return sent, manager, vehicle


def _scenario_responses() -> dict[str, Any]:
    spec = json.loads((FIXTURES_DIR / "updates" / SCENARIO).read_text())
    return _resolve(spec["responses"])


def test_flag0_fallback_serves_ccs2_status() -> None:
    sent, _manager, vehicle = _run(_scenario_responses())

    requests_seen = [f"{r['method']} {urlparse(r['url']).path}" for r in sent]
    assert requests_seen == [
        "GET /api/v1/spa/vehicles/VEHICLE-1/status/latest",
        "GET /api/v1/spa/vehicles/VEHICLE-1/ccs2/carstatus/latest",
        "GET /api/v1/spa/vehicles/VEHICLE-1/location/park",
        "POST /api/v1/spa/vehicles/VEHICLE-1/drvhistory",
    ]
    # The protocol header follows the endpoint that was actually read.
    assert sent[0]["headers"]["Ccuccs2protocolsupport"] == "0"
    assert sent[1]["headers"]["Ccuccs2protocolsupport"] == "1"
    # The vehicle flag stays 0: it also selects the remote-command endpoints.
    assert vehicle.ccu_ccs2_protocol_support == 0


def test_fallback_propagates_when_ccs2_also_unavailable() -> None:
    responses = _scenario_responses()
    responses["GET /vehicles/VEHICLE-1/ccs2/carstatus/latest"] = {"body": FIFTY31}

    with pytest.raises(ServiceTemporaryUnavailable):
        _run(responses)


def test_fallback_not_triggered_by_timeout() -> None:
    responses = _scenario_responses()
    responses["GET /vehicles/VEHICLE-1/status/latest"] = {"body": TIMEOUT}

    with pytest.raises(RequestTimeoutError):
        _run(responses)
