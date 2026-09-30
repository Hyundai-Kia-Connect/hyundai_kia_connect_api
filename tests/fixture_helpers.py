"""Helpers for loading JSON test fixtures and parsing them into Vehicles.

These are plain functions (not pytest fixtures) so they can be imported
directly by test modules at module level for use with ``pytest.mark.parametrize``.

Every fixture declares which parser handles it via ``_fixture_meta.parser``,
a key into :data:`PARSERS`. Adding a fixture for an already-supported API
needs no test code; adding a new API needs one ``PARSERS`` entry.
"""

from __future__ import annotations

import dataclasses
import json
import pathlib
from typing import Any

from hyundai_kia_connect_api.ApiImplType1 import ApiImplType1
from hyundai_kia_connect_api.HyundaiBlueLinkApiBR import HyundaiBlueLinkApiBR
from hyundai_kia_connect_api.HyundaiBlueLinkApiUSA import HyundaiBlueLinkApiUSA
from hyundai_kia_connect_api.KiaCciApiEU import KiaCciApiEU
from hyundai_kia_connect_api.KiaUvoApiAU import KiaUvoApiAU
from hyundai_kia_connect_api.KiaUvoApiCA import KiaUvoApiCA
from hyundai_kia_connect_api.KiaUvoApiCN import KiaUvoApiCN
from hyundai_kia_connect_api.KiaUvoApiEU import KiaUvoApiEU
from hyundai_kia_connect_api.KiaUvoApiUSA import KiaUvoApiUSA
from hyundai_kia_connect_api.Vehicle import Vehicle

FIXTURES_DIR = pathlib.Path(__file__).parent / "fixtures"


def load_fixture(filename: str) -> dict:
    """Load a JSON fixture file from the fixtures directory."""
    filepath = FIXTURES_DIR / filename
    with open(filepath, encoding="utf-8") as f:
        return json.load(f)


def discover_fixtures(prefix: str = "") -> list[str]:
    """Discover all fixture files matching a prefix (all fixtures by default)."""
    return sorted(p.name for p in FIXTURES_DIR.glob(f"{prefix}*.json"))


def get_fixture_expected(fixture_data: dict) -> dict:
    """Return the ``_fixture_meta.expected`` block from a fixture."""
    return fixture_data.get("_fixture_meta", {}).get("expected", {})


def get_fixture_meta(fixture_data: dict) -> dict:
    """Return the ``_fixture_meta`` block from a fixture."""
    return fixture_data.get("_fixture_meta", {})


def bare_api(api_class: type, **attrs: Any):
    """Build an API instance without running ``__init__`` (no network/session).

    Only the attributes the parsers read need to be set; class-level defaults
    such as ``data_timezone`` are inherited unless overridden in ``attrs``.
    """
    api = api_class.__new__(api_class)
    for name, value in attrs.items():
        setattr(api, name, value)
    return api


@dataclasses.dataclass(frozen=True)
class Parser:
    """How to turn a fixture payload into a parsed ``Vehicle``."""

    api_class: type
    method: str = "_update_vehicle_properties"
    api_attrs: dict[str, Any] = dataclasses.field(default_factory=dict)
    # Attributes preset on the Vehicle before parsing (e.g. CA picks its
    # temperature range by model year).
    vehicle_attrs: dict[str, Any] = dataclasses.field(default_factory=dict)
    # Keys to descend into the fixture before handing it to the parser.
    state_path: tuple[str, ...] = ()
    # Where the parser stores the raw payload: None means ``vehicle.data`` is
    # the payload itself, otherwise ``vehicle.data[raw_data_key]`` holds
    # ``payload[raw_data_key]``.
    raw_data_key: str | None = None

    def api(self):
        return bare_api(self.api_class, **self.api_attrs)


_CCS2_TEMPERATURE_RANGE = [x * 0.5 for x in range(28, 60)]

PARSERS: dict[str, Parser] = {
    "usa_kia": Parser(
        KiaUvoApiUSA,
        api_attrs={
            "data_timezone": None,
            "temperature_range": [62, 64, 66, 68, 70, 72, 74, 76, 78, 80, 82],
        },
    ),
    "usa_hyundai": Parser(
        HyundaiBlueLinkApiUSA,
        api_attrs={"data_timezone": None, "temperature_range": range(62, 82)},
    ),
    "eu": Parser(KiaUvoApiEU),
    "ccs2": Parser(
        ApiImplType1,
        method="_update_vehicle_properties_ccs2",
        api_attrs={
            "data_timezone": None,
            "temperature_range": _CCS2_TEMPERATURE_RANGE,
        },
    ),
    "eu_kia_cci": Parser(KiaCciApiEU, method="_update_vehicle_properties_ccs2"),
    "br": Parser(
        HyundaiBlueLinkApiBR,
        method="_update_vehicle_properties_ccs2",
        state_path=("resMsg", "state", "Vehicle"),
    ),
    "ca": Parser(
        KiaUvoApiCA,
        method="_update_vehicle_properties_base",
        vehicle_attrs={"year": 2022},
        raw_data_key="status",
    ),
    "au": Parser(KiaUvoApiAU),
    "cn": Parser(KiaUvoApiCN),
}


def fixture_parser(filename: str) -> Parser:
    """Return the :class:`Parser` a fixture declares in ``_fixture_meta.parser``."""
    return PARSERS[get_fixture_meta(load_fixture(filename))["parser"]]


def fixture_payload(filename: str) -> dict:
    """Return the part of the fixture that is handed to its parser."""
    payload = load_fixture(filename)
    for key in fixture_parser(filename).state_path:
        payload = payload[key]
    return payload


def parse_fixture(filename: str) -> tuple[Vehicle, dict]:
    """Parse a fixture with its declared parser; return ``(vehicle, payload)``."""
    parser = fixture_parser(filename)
    payload = fixture_payload(filename)
    vehicle = Vehicle()
    for name, value in parser.vehicle_attrs.items():
        setattr(vehicle, name, value)
    getattr(parser.api(), parser.method)(vehicle, payload)
    return vehicle, payload
