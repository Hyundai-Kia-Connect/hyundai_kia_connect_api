"""Kia EU CCI control gate — evidence-mapped.

The verified set now covers the 2026-10 vehicle run (EV6, EV3, eNiro
2021, Inster 2026): hazard lights (light), horn + hazard (horn-light),
charge start/stop and the granular door-lock/door-unlock endpoints
joined door, temperature (PV5, 2026-09-19) and lamp (EV6, 2026-09-23).
Everything else stays gated until live verification.
"""

import datetime as dt
from unittest.mock import MagicMock, patch

import pytest

from hyundai_kia_connect_api.ApiImpl import ClimateRequestOptions
from hyundai_kia_connect_api.const import CHARGE_PORT_ACTION, VEHICLE_LOCK_ACTION
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


def test_kia_lock_unlock_granular():
    """Lock/unlock route to the granular per-action endpoints (the EU
    apps abandoned the shared door + close/open shape). Accepted live
    across the 2026-10 vehicle run."""
    api = KiaCciApiEU(9, 2, "en")
    token = _make_token()
    vehicle = _make_vehicle()
    for action, endpoint in (
        (VEHICLE_LOCK_ACTION.LOCK, "door-lock"),
        (VEHICLE_LOCK_ACTION.UNLOCK, "door-unlock"),
    ):
        with (
            patch("hyundai_kia_connect_api.GspaApiEU.requests.post") as post,
            patch.object(KiaCciApiEU, "_get_control_token") as get_ct,
        ):
            get_ct.return_value = ("Bearer ctrl-token-abc", 4_000_000_000)
            post.return_value = MagicMock(status_code=200, json=lambda: ENVELOPE)
            action_id = api.lock_action(token, vehicle, action)
        assert action_id == "gspa:sid-1"
        assert post.call_args.args[0].endswith(
            f"/gspa/v1/remote/vehicles/test123/{endpoint}"
        )
        assert post.call_args.kwargs["json"] == {"command": "set"}


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
    for meth, args in (
        (api.door_power_off, ()),
        (api.open_frunk, ()),
        (api.charge_port_action, (CHARGE_PORT_ACTION.OPEN,)),
    ):
        with (
            patch("hyundai_kia_connect_api.GspaApiEU.requests.post") as post,
            pytest.raises(NotImplementedError),
        ):
            meth(token, vehicle, *args)
        post.assert_not_called()


def test_kia_check_action_status_is_gated():
    """Kia action polling stays gated (D6): update-status on Kia is not
    a poll — it triggers a new status upload itself (live PV5)."""
    api = KiaCciApiEU(9, 2, "en")
    with pytest.raises(NotImplementedError):
        api.check_action_status(_make_token(), _make_vehicle(), "gspa:sid-1")


def test_kia_windows_accepted():
    """Kia EU window control ships: the server accepted the scope
    command with a polling SID on the EV6 (2026-10-18)."""
    api = KiaCciApiEU(9, 2, "en")
    assert api.supports_window_control is True
    token = _make_token()
    vehicle = _make_vehicle()
    from hyundai_kia_connect_api.ApiImpl import WindowRequestOptions
    from hyundai_kia_connect_api.const import WINDOW_STATE

    vehicle.supports_window_control = True
    options = WindowRequestOptions(
        front_left=WINDOW_STATE.CLOSED,
        front_right=WINDOW_STATE.CLOSED,
        back_left=WINDOW_STATE.CLOSED,
        back_right=WINDOW_STATE.CLOSED,
    )
    with (
        patch("hyundai_kia_connect_api.GspaApiEU.requests.post") as post,
        patch.object(KiaCciApiEU, "_get_control_token") as get_ct,
    ):
        get_ct.return_value = ("Bearer ctrl-token-abc", 4_000_000_000)
        post.return_value = MagicMock(status_code=200, json=lambda: ENVELOPE)
        action_id = api.set_windows_state(token, vehicle, options)
    assert action_id == "gspa:sid-1"
    assert post.call_args.args[0].endswith("/window-curtain")
