"""Cross-region contract and snapshot tests for vehicle commands.

Every implementation runs every command against a fake HTTP transport. Each
call must return a non-empty action id, and the outbound requests (method,
URL, body) are compared to a syrupy snapshot, so any change to what a region
sends shows up as a snapshot diff.

To update snapshots after an intentional change::

    pytest tests/test_vehicle_commands.py --snapshot-update
"""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Callable
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import pytest
import requests

from hyundai_kia_connect_api.ApiImpl import (
    ApiImpl,
    ClimateRequestOptions,
    ScheduleChargingClimateRequestOptions,
)
from hyundai_kia_connect_api.const import (
    BRAND_HYUNDAI,
    BRAND_KIA,
    BRANDS,
    ENGINE_TYPES,
    REGION_AUSTRALIA,
    REGION_BRAZIL,
    REGION_CANADA,
    REGION_CHINA,
    REGION_EUROPE,
    REGION_EUROPE_CCI,
    REGION_INDIA,
    REGION_USA,
    REGIONS,
    VEHICLE_LOCK_ACTION,
)
from hyundai_kia_connect_api.exceptions import APIError, UnsupportedControlError
from hyundai_kia_connect_api.Token import Token
from hyundai_kia_connect_api.Vehicle import Vehicle
from hyundai_kia_connect_api.VehicleManager import VehicleManager

if TYPE_CHECKING:
    from syrupy.assertion import SnapshotAssertion

# One response body that every region's success check accepts.
_RESPONSE_BODY = {
    "responseHeader": {"responseCode": 0},
    "retCode": "S",
    "resCode": "0000",
    "msgId": "MSG-1",
    "SID": "SID-1",
    "status": {"statusCode": 0, "errorType": 0, "errorCode": 0},
    "result": {"pAuth": "pin-auth"},
    "resMsg": {"controlToken": "control", "expiresTime": 600},
    "controlToken": "control",
    "expiresTime": 600,
    "isMatched": True,
    "controlTokenInfo": {"controlToken": "control", "expiresTime": 600},
}
_RESPONSE_HEADERS = {"transactionId": "TX-1", "Xid": "TX-1"}

# (region, brand, CCS2 protocol) for every implementation.
IMPLEMENTATIONS = {
    "eu_kia": (REGION_EUROPE, BRAND_KIA, 0),
    "eu_kia_ccs2": (REGION_EUROPE, BRAND_KIA, 1),
    "eu_cci_kia": (REGION_EUROPE_CCI, BRAND_KIA, 1),
    "eu_cci_hyundai": (REGION_EUROPE_CCI, BRAND_HYUNDAI, 1),
    "ca_kia": (REGION_CANADA, BRAND_KIA, 0),
    "usa_kia": (REGION_USA, BRAND_KIA, 0),
    "usa_hyundai": (REGION_USA, BRAND_HYUNDAI, 0),
    "cn_kia": (REGION_CHINA, BRAND_KIA, 0),
    "au_hyundai": (REGION_AUSTRALIA, BRAND_HYUNDAI, 0),
    "au_kia_ice_ccs2": (REGION_AUSTRALIA, BRAND_KIA, 1, ENGINE_TYPES.ICE),
    "in_hyundai": (REGION_INDIA, BRAND_HYUNDAI, 0),
    "br_hyundai": (REGION_BRAZIL, BRAND_HYUNDAI, 0),
}

COMMANDS: dict[str, Callable[[ApiImpl, Token, Vehicle], Any]] = {
    "lock": lambda api, t, v: api.lock_action(t, v, VEHICLE_LOCK_ACTION.LOCK),
    "unlock": lambda api, t, v: api.lock_action(t, v, VEHICLE_LOCK_ACTION.UNLOCK),
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

# Commands an implementation doesn't support must raise NotImplementedError.
NOT_SUPPORTED = {
    ("br_hyundai", "start_charge"),
    ("br_hyundai", "stop_charge"),
    ("br_hyundai", "set_charge_limits"),
}


def _key(mapping: dict[int, str], value: str) -> int:
    return next(k for k, v in mapping.items() if v == value)


@pytest.fixture
def sent_requests() -> Any:
    """Patch every requests.Session call; yield the list of requests sent."""
    sent: list[dict[str, Any]] = []

    def fake_request(
        self: requests.Session, method: str, url: str, **kwargs: Any
    ) -> requests.Response:
        # Round-trip through JSON so the snapshot holds what goes on the wire.
        body = kwargs.get("json")
        if body is not None:
            body = json.loads(json.dumps(body))
        elif isinstance(kwargs.get("data"), str):
            body = json.loads(kwargs["data"])
        sent.append({"method": method.upper(), "url": url, "body": body})
        response = requests.Response()
        response.status_code = 200
        response.url = url
        response.headers.update(_RESPONSE_HEADERS)
        response._content = json.dumps(_RESPONSE_BODY).encode()
        return response

    with patch.object(requests.Session, "request", fake_request):
        yield sent


@pytest.fixture
def command_response() -> Any:
    """Patch Session.request to answer every call with a chosen JSON body.

    Yields (sent, holder): assign the wanted canned response to
    ``holder["body"]`` before calling the command.
    """
    sent: list[dict[str, Any]] = []
    holder: dict[str, Any] = {"body": {}}

    def fake_request(
        self: requests.Session, method: str, url: str, **kwargs: Any
    ) -> requests.Response:
        payload = kwargs.get("json")
        if payload is None and isinstance(kwargs.get("data"), str):
            payload = json.loads(kwargs["data"])
        sent.append({"method": method.upper(), "url": url, "body": payload})
        # vrfypin must succeed so the flow reaches the command itself.
        answer = _RESPONSE_BODY if "vrfypin" in url else holder["body"]
        response = requests.Response()
        response.status_code = 200
        response.url = url
        response.headers.update(_RESPONSE_HEADERS)
        response._content = json.dumps(answer).encode()
        return response

    with patch.object(requests.Session, "request", fake_request):
        yield sent, holder


@pytest.mark.parametrize("command", COMMANDS)
@pytest.mark.parametrize("impl", IMPLEMENTATIONS)
def test_vehicle_command(
    impl: str,
    command: str,
    sent_requests: list[dict[str, Any]],
    snapshot: SnapshotAssertion,
) -> None:
    region, brand, ccs2, *rest = IMPLEMENTATIONS[impl]
    engine_type = rest[0] if rest else ENGINE_TYPES.EV
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
    vehicle = Vehicle(
        id="VEHICLE-ID",
        VIN="VIN0000000000000",
        year=2024,
        engine_type=engine_type,
        ccu_ccs2_protocol_support=ccs2,
    )

    if (impl, command) in NOT_SUPPORTED:
        with pytest.raises(NotImplementedError):
            COMMANDS[command](api, token, vehicle)
        assert sent_requests == []
        return

    action_id = COMMANDS[command](api, token, vehicle)

    assert isinstance(action_id, str)
    assert action_id
    assert {"action_id": action_id, "requests": sent_requests} == snapshot


@pytest.mark.parametrize("generation", ["G3", "G1", None])
@pytest.mark.parametrize("brand", [BRAND_HYUNDAI, BRAND_KIA])
def test_ca_ev_climate_payload_by_generation(
    brand: str,
    generation: str | None,
    sent_requests: list[dict[str, Any]],
    snapshot: SnapshotAssertion,
) -> None:
    api = VehicleManager.get_implementation_by_region_brand(
        _key(REGIONS, REGION_CANADA), _key(BRANDS, brand), "en"
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
    vehicle = Vehicle(
        id="VEHICLE-ID",
        VIN="VIN0000000000000",
        year=2027,
        model="IONIQ9",
        engine_type=ENGINE_TYPES.EV,
        generation=generation,
    )
    COMMANDS["start_climate"](api, token, vehicle)
    body = sent_requests[-1]["body"]
    assert sent_requests[-1]["url"].endswith("evc/rfon")
    expected, other = (
        ("remoteControl", "hvacInfo")
        if generation == "G3"
        else ("hvacInfo", "remoteControl")
    )
    assert expected in body
    assert other not in body
    assert {"requests": sent_requests} == snapshot


_CA_REJECTION = {
    "error": {"errorCode": "15109"},
    "responseHeader": {"responseCode": 1, "responseDesc": "Failure"},
}


def test_ca_start_climate_rejection_raises(
    command_response: tuple[list[dict[str, Any]], dict[str, Any]],
) -> None:
    api = VehicleManager.get_implementation_by_region_brand(
        _key(REGIONS, REGION_CANADA), _key(BRANDS, BRAND_HYUNDAI), "en"
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
    vehicle = Vehicle(
        id="VEHICLE-ID",
        VIN="VIN0000000000000",
        year=2027,
        model="IONIQ9",
        engine_type=ENGINE_TYPES.EV,
    )
    _sent, body_holder = command_response
    body_holder["body"] = _CA_REJECTION
    with pytest.raises(UnsupportedControlError, match="15109"):
        COMMANDS["start_climate"](api, token, vehicle)


def test_ca_command_response_without_envelope_raises(
    command_response: tuple[list[dict[str, Any]], dict[str, Any]],
) -> None:
    """Shape per the shared method's pinned contract: a missing envelope raises."""
    api = VehicleManager.get_implementation_by_region_brand(
        _key(REGIONS, REGION_CANADA), _key(BRANDS, BRAND_HYUNDAI), "en"
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
    vehicle = Vehicle(
        id="VEHICLE-ID",
        VIN="VIN0000000000000",
        year=2027,
        model="IONIQ9",
        engine_type=ENGINE_TYPES.EV,
    )
    _sent, body_holder = command_response
    body_holder["body"] = {"result": {"foo": "bar"}}
    with pytest.raises(APIError):
        COMMANDS["start_climate"](api, token, vehicle)


def test_ca_command_response_null_header_passes(
    command_response: tuple[list[dict[str, Any]], dict[str, Any]],
) -> None:
    api = VehicleManager.get_implementation_by_region_brand(
        _key(REGIONS, REGION_CANADA), _key(BRANDS, BRAND_HYUNDAI), "en"
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
    vehicle = Vehicle(
        id="VEHICLE-ID",
        VIN="VIN0000000000000",
        year=2027,
        model="IONIQ9",
        engine_type=ENGINE_TYPES.EV,
    )
    _sent, body_holder = command_response
    body_holder["body"] = {"responseHeader": None, "result": {"foo": "bar"}}
    action_id = COMMANDS["start_climate"](api, token, vehicle)
    assert isinstance(action_id, str)
    assert action_id


@pytest.mark.parametrize(
    "impl, seat_options",
    [
        (
            "eu_kia",
            ClimateRequestOptions(
                set_temp=21,
                duration=10,
                climate=True,
                heating=0,
                front_left_seat=3,
                front_right_seat=2,
                rear_left_seat=1,
                steering_wheel=1,
            ),
        ),
    ],
)
@pytest.mark.parametrize("command", ["start_climate"])
def test_legacy_climate_seat_options(
    impl: str,
    seat_options: ClimateRequestOptions,
    command: str,
    sent_requests: list[dict[str, Any]],
    snapshot: SnapshotAssertion,
) -> None:
    region, brand, ccs2, *_ = IMPLEMENTATIONS[impl]
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
    vehicle = Vehicle(
        id="VEHICLE-ID",
        VIN="VIN0000000000000",
        year=2024,
        engine_type=ENGINE_TYPES.HEV,
        ccu_ccs2_protocol_support=ccs2,
    )
    api.start_climate(token, vehicle, seat_options)
    body = sent_requests[-1]["body"]
    assert body["options"]["seatHeaterVentCMD"] == {
        "drvSeatOptCmd": 3,
        "astSeatOptCmd": 2,
        "rlSeatOptCmd": 1,
    }
    assert body["options"]["wheelHeating"] == 1
    assert {"requests": sent_requests} == snapshot


def _ca_kia_ev() -> tuple[ApiImpl, Token, Vehicle]:
    api = VehicleManager.get_implementation_by_region_brand(
        _key(REGIONS, REGION_CANADA), _key(BRANDS, BRAND_KIA), "en"
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
    vehicle = Vehicle(
        id="VEHICLE-ID", VIN="VIN0000000000000", year=2027, engine_type=ENGINE_TYPES.EV
    )
    return api, token, vehicle


@pytest.mark.parametrize(
    "options",
    [
        ScheduleChargingClimateRequestOptions(
            charging_enabled=True,
            off_peak_start_time=dt.time(20, 0),
            off_peak_end_time=dt.time(16, 0),
            off_peak_charge_only_enabled=True,
        ),
        ScheduleChargingClimateRequestOptions(
            charging_enabled=True,
            off_peak_start_time=dt.time(0, 0),
            off_peak_end_time=dt.time(12, 30),
            off_peak_charge_only_enabled=False,
        ),
        # Only the flag: the window and mode come from the vehicle.
        ScheduleChargingClimateRequestOptions(charging_enabled=False),
    ],
    ids=["window", "midnight_noon", "disable_keeps_window"],
)
def test_ca_schedule_charge_payload(
    options: ScheduleChargingClimateRequestOptions,
    sent_requests: list[dict[str, Any]],
    snapshot: SnapshotAssertion,
) -> None:
    api, token, vehicle = _ca_kia_ev()
    vehicle.ev_schedule_charge_enabled = True
    vehicle.ev_off_peak_charge_only_enabled = True
    vehicle.ev_off_peak_start_time = dt.time(20, 0)
    vehicle.ev_off_peak_end_time = dt.time(16, 0)
    assert api.schedule_charging_and_climate(token, vehicle, options) == "TX-1"
    assert sent_requests[-1]["url"].endswith("v2/rc/ev/reschargeset")
    assert {"requests": sent_requests} == snapshot


def test_ca_schedule_rejects_climate_scope(
    sent_requests: list[dict[str, Any]],
) -> None:
    api, token, vehicle = _ca_kia_ev()
    with pytest.raises(NotImplementedError):
        api.schedule_charging_and_climate(
            token, vehicle, ScheduleChargingClimateRequestOptions(climate_enabled=True)
        )
    assert sent_requests == []
