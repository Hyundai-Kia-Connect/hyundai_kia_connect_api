"""Kia EU CCI control gate — evidence-mapped.

Only live-proven endpoints pass (GSPA_VERIFIED_ENDPOINTS). Door lock and
unlock via the shared "door" endpoint + {"command": "close"/"open"} were
live-proven on a Kia PV5 (2026-09-19: 202 S 202-000, fresh stored-status
7 s / 4 s after sending), and climate start/stop via the "temperature"
endpoint on the same PV5 (202 S 202-000 both directions), and lamp
all-off on a Kia EV6 (2026-09-23: 202 APPLIED with the stored-status
lastUpdateTime advancing). Everything else
stays gated until live verification (D6).
"""

import datetime as dt
from unittest.mock import MagicMock, patch

import pytest

from hyundai_kia_connect_api.ApiImpl import ClimateRequestOptions
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


def test_kia_lock_unlock_passes_gate():
    """LOCK and UNLOCK (door + close/open) are live-proven on a PV5."""
    api = KiaCciApiEU(9, 2, "en")
    token = _make_token()
    vehicle = _make_vehicle()
    for action, command in (
        (VEHICLE_LOCK_ACTION.LOCK, "close"),
        (VEHICLE_LOCK_ACTION.UNLOCK, "open"),
    ):
        with (
            patch("hyundai_kia_connect_api.GspaApiEU.requests.post") as post,
            patch.object(KiaCciApiEU, "_get_control_token") as get_ct,
        ):
            get_ct.return_value = ("Bearer ctrl-token-abc", 4_000_000_000)
            post.return_value = MagicMock(status_code=200, json=lambda: ENVELOPE)
            action_id = api.lock_action(token, vehicle, action)
        assert action_id == "gspa:sid-1"
        assert post.call_args.args[0].endswith("/gspa/v1/remote/vehicles/test123/door")
        assert post.call_args.kwargs["json"] == {"command": command}


def test_kia_climate_passes_gate():
    """Climate start/stop (temperature endpoint) is live-proven on a PV5."""
    api = KiaCciApiEU(9, 2, "en")
    token = _make_token()
    vehicle = _make_vehicle()
    for method, body in (
        (
            api.start_climate,
            {
                "command": "start",
                "hvacTemp": "22.0",
                "tempUnit": "C",
                "hvacTempType": 1,
            },
        ),
        (api.stop_climate, {"command": "stop"}),
    ):
        with (
            patch("hyundai_kia_connect_api.GspaApiEU.requests.post") as post,
            patch.object(KiaCciApiEU, "_get_control_token") as get_ct,
        ):
            get_ct.return_value = ("Bearer ctrl-token-abc", 4_000_000_000)
            post.return_value = MagicMock(status_code=200, json=lambda: ENVELOPE)
            if method == api.start_climate:
                action_id = method(token, vehicle, ClimateRequestOptions(set_temp=22.0))
            else:
                action_id = method(token, vehicle)
        assert action_id == "gspa:sid-1"
        assert post.call_args.args[0].endswith(
            "/gspa/v1/remote/vehicles/test123/temperature"
        )
        assert post.call_args.kwargs["json"] == body


def test_kia_lamp_passes_gate():
    """Lamp all-off is live-proven on a Kia EV6 (2026-09-23: 202 APPLIED
    with the stored-status lastUpdateTime advancing)."""
    api = KiaCciApiEU(9, 2, "en")
    token = _make_token()
    vehicle = _make_vehicle()
    with (
        patch("hyundai_kia_connect_api.GspaApiEU.requests.post") as post,
        patch.object(KiaCciApiEU, "_get_control_token") as get_ct,
    ):
        get_ct.return_value = ("Bearer ctrl-token-abc", 4_000_000_000)
        post.return_value = MagicMock(status_code=200, json=lambda: ENVELOPE)
        action_id = api.turn_off_lamp(token, vehicle, "all-off")
    assert action_id == "gspa:sid-1"
    assert post.call_args.args[0].endswith("/gspa/v1/remote/vehicles/test123/lamp")
    assert post.call_args.kwargs["json"] == {"command": "all-off"}


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
