"""Kia EU CCI control gate — evidence-mapped.

Only live-proven endpoints pass (GSPA_VERIFIED_ENDPOINTS). Door lock via
the shared "door" endpoint + {"command": "close"} was live-proven on a
Kia PV5 (2026-09-19: 202 S 202-000, hazard flash, fresh stored-status
7 s later). Everything else stays gated until live verification (D6).
"""

import datetime as dt
from unittest.mock import MagicMock, patch

import pytest

from hyundai_kia_connect_api.const import VEHICLE_LOCK_ACTION
from hyundai_kia_connect_api.KiaCciApiEU import KiaCciApiEU
from hyundai_kia_connect_api.Token import Token
from hyundai_kia_connect_api.Vehicle import Vehicle

ENVELOPE = {"rc": "0000", "rs": {"SID": "sid-1"}, "msg": "success"}


def _make_api() -> KiaCciApiEU:
    return KiaCciApiEU(9, 2, "en")


def _make_token() -> Token:
    token = Token(
        username="user@test.com",
        password="MyPassword123!",
        pin="1234",
        access_token="Bearer ccs-token",
        refresh_token="REFRESHTOKEN1234567890123456789012345678901234567890",
        device_id="12345678-1234-1234-1234-123456789abc",
        valid_until=dt.datetime.now(dt.UTC) + dt.timedelta(hours=1),
        user_id="test-uid-123",
    )
    token.control_token = None
    return token


def _make_vehicle() -> Vehicle:
    vehicle = Vehicle()
    vehicle.id = "test123"
    vehicle.ccu_ccs2_protocol_support = 2
    return vehicle


def test_kia_door_methods_are_gated():
    api = KiaCciApiEU(9, 1, "en")
    for meth in (
        api.lock_door,
        api.unlock_door,
        api.lock_door_safety,
        api.unlock_door_safety,
    ):
        with pytest.raises(NotImplementedError):
            meth(None, None)


def test_kia_lock_lock_passes_gate():
    """LOCK (door + close) is live-proven: the gate lets it through."""
    api = _make_api()
    token = _make_token()
    vehicle = _make_vehicle()
    with (
        patch("hyundai_kia_connect_api.GspaApiEU.requests.post") as post,
        patch.object(KiaCciApiEU, "_get_control_token") as get_ct,
    ):
        get_ct.return_value = ("Bearer ctrl-token-abc", 4_000_000_000)
        post.return_value = MagicMock(status_code=200, json=lambda: ENVELOPE)
        action_id = api.lock_action(token, vehicle, VEHICLE_LOCK_ACTION.LOCK)
    assert action_id == "gspa:sid-1"
    assert post.call_args.args[0].endswith("/gspa/v1/remote/vehicles/test123/door")
    assert post.call_args.kwargs["json"] == {"command": "close"}


def test_kia_lock_unlock_is_gated():
    """Unlock was NOT exercised live on a Kia vehicle — stays gated."""
    api = KiaCciApiEU(9, 2, "en")
    with pytest.raises(NotImplementedError):
        api.lock_action(_make_token(), _make_vehicle(), VEHICLE_LOCK_ACTION.UNLOCK)


def test_kia_unverified_commands_are_gated():
    """Commands without live proof raise before any request is sent."""
    api = KiaCciApiEU(9, 2, "en")
    token = _make_token()
    vehicle = _make_vehicle()
    for meth in (
        api.start_charge,
        api.stop_charge,
        api.door_power_off,
        api.start_hazard_lights,
        lambda t, v: api.lock_door_safety(t, v),
    ):
        with (
            patch("hyundai_kia_connect_api.GspaApiEU.requests.post") as post,
            pytest.raises(NotImplementedError),
        ):
            meth(token, vehicle)
        post.assert_not_called()


def test_kia_check_action_status_is_gated():
    """Kia action polling stays gated (D6): update-status on Kia is not
    a poll — it triggers a new status upload itself (live PV5)."""
    api = KiaCciApiEU(9, 2, "en")
    with pytest.raises(NotImplementedError):
        api.check_action_status(_make_token(), _make_vehicle(), "gspa:sid-1")
