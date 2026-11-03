"""Cross-region snapshot tests for VehicleManager.update_vehicle_with_cached_state.

Each scenario in ``tests/fixtures/updates`` names an implementation, the
vehicle to update and the response for every request the update makes. The
update runs through a fake HTTP transport that answers by method and URL
path, and the requests sent plus the full parsed Vehicle are compared to a
syrupy snapshot. A request with no scenario entry, or a scenario response
that is never requested, fails the test.

To update snapshots after an intentional change::

    pytest tests/test_vehicle_update_snapshots.py --snapshot-update
"""

from __future__ import annotations

import datetime as dt
import json
from typing import TYPE_CHECKING, Any
from unittest.mock import patch
from urllib.parse import urlparse

import pytest
import requests

from hyundai_kia_connect_api.const import BRANDS, ENGINE_TYPES, REGIONS
from hyundai_kia_connect_api.Token import Token
from hyundai_kia_connect_api.Vehicle import Vehicle
from hyundai_kia_connect_api.VehicleManager import VehicleManager
from tests.fixture_helpers import FIXTURES_DIR, discover_fixtures
from tests.vehicle_snapshot_serializer import vehicle_to_dict

if TYPE_CHECKING:
    from syrupy.assertion import SnapshotAssertion

UPDATES_DIR = FIXTURES_DIR / "updates"
SCENARIOS = sorted(p.stem for p in UPDATES_DIR.glob("*.json"))
REF = "$fixture"


def _key(mapping: dict[int, str], value: str) -> int:
    return next(k for k, v in mapping.items() if v == value)


def _load(ref: str) -> Any:
    """Load ``<path under tests/fixtures>[#key]`` without its _fixture_meta."""
    path, _, key = ref.partition("#")
    data = json.loads((FIXTURES_DIR / path).read_text())
    data.pop("_fixture_meta", None)
    return data[key] if key else data


def _resolve(value: Any) -> Any:
    """Replace ``"$fixture:<ref>"`` and ``{"$fixture": <ref>, ...overrides}``."""
    if isinstance(value, str) and value.startswith(REF + ":"):
        return _resolve(_load(value[len(REF) + 1 :]))
    if isinstance(value, dict):
        if REF in value:
            base = _resolve(_load(value[REF]))
            base.update({k: _resolve(v) for k, v in value.items() if k != REF})
            return base
        return {k: _resolve(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve(v) for v in value]
    return value


def _vehicle(spec: dict[str, Any]) -> Vehicle:
    spec = dict(spec)
    if spec.get("engine_type"):
        spec["engine_type"] = ENGINE_TYPES[spec["engine_type"]]
    return Vehicle(**spec)


def _refs(value: Any) -> list[str]:
    """Fixture paths a scenario value references, as ``_resolve`` reads them."""
    if isinstance(value, str) and value.startswith(REF + ":"):
        return [value[len(REF) + 1 :].partition("#")[0]]
    if isinstance(value, dict):
        own = [value[REF].partition("#")[0]] if REF in value else []
        return own + [r for k, v in value.items() if k != REF for r in _refs(v)]
    if isinstance(value, list):
        return [r for v in value for r in _refs(v)]
    return []


def test_every_status_fixture_has_a_scenario() -> None:
    referenced = {
        ref
        for scenario in SCENARIOS
        for ref in _refs(json.loads((UPDATES_DIR / f"{scenario}.json").read_text()))
    }
    assert set(discover_fixtures("")) <= referenced


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_vehicle_update(scenario: str, snapshot: SnapshotAssertion) -> None:
    spec = json.loads((UPDATES_DIR / f"{scenario}.json").read_text())
    # Each entry is "<METHOD> <path suffix>" -> one response, or a list
    # answered in order. A response is {"status": int, "body": ...}.
    pending = {
        key: list(value) if isinstance(value, list) else [value]
        for key, value in spec["responses"].items()
    }
    sent: list[dict[str, Any]] = []

    def fake_request(
        self: requests.Session, method: str, url: str, **kwargs: Any
    ) -> requests.Response:
        path = urlparse(url).path
        sent.append({"method": method.upper(), "url": url, "json": kwargs.get("json")})
        matches = [
            k
            for k in pending
            if k.split(" ", 1)[0] == method.upper()
            and path.endswith(k.split(" ", 1)[1])
        ]
        assert matches, f"{scenario}: unexpected request {method.upper()} {url}"
        assert pending[matches[0]], f"{scenario}: no response left for {matches[0]}"
        answer = pending[matches[0]].pop(0)
        response = requests.Response()
        response.status_code = answer.get("status", 200)
        response.url = url
        body = _resolve(answer.get("body"))
        response._content = b"" if body is None else json.dumps(body).encode()
        return response

    impl = spec["implementation"]
    manager = VehicleManager(
        region=_key(REGIONS, impl["region"]),
        brand=_key(BRANDS, impl["brand"]),
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
    vehicle = _vehicle(spec["vehicle"])
    manager.vehicles = {vehicle.id: vehicle}

    with patch.object(requests.Session, "request", fake_request):
        manager.update_vehicle_with_cached_state(vehicle.id)

    unused = {k: v for k, v in pending.items() if v}
    assert not unused, f"{scenario}: responses never requested: {list(unused)}"
    assert vehicle.last_scanned_at is not None
    result = vehicle_to_dict(vehicle)
    result.pop("last_scanned_at")
    assert {"requests": sent, "vehicle": result} == snapshot
