"""Shared transport hooks preserve the established legacy and CCS2 commands."""

from unittest.mock import Mock

import pytest

from hyundai_kia_connect_api.ApiImplType1 import ApiImplType1
from hyundai_kia_connect_api.const import VEHICLE_LOCK_ACTION
from hyundai_kia_connect_api.Token import Token
from hyundai_kia_connect_api.Vehicle import Vehicle


@pytest.mark.parametrize("protocol", [0, 1])
@pytest.mark.parametrize(
    "method,args,endpoint,command",
    [
        ("lock_action", (VEHICLE_LOCK_ACTION.LOCK,), "door", "close"),
        ("lock_action", (VEHICLE_LOCK_ACTION.UNLOCK,), "door", "open"),
        ("start_charge", (), "charge", "start"),
        ("stop_charge", (), "charge", "stop"),
        ("stop_climate", (), "temperature", "stop"),
        ("start_hazard_lights", (), "light", "on"),
    ],
)
def test_shared_commands_retain_legacy_and_ccs2_contracts(
    protocol, method, args, endpoint, command
):
    api = ApiImplType1()
    api.SPA_API_URL = "https://synthetic.invalid/api/v1/spa/"
    api.SPA_API_URL_V2 = "https://synthetic.invalid/api/v2/spa/"
    api.session = Mock()
    api.session.post.return_value.json.return_value = {
        "retCode": "S",
        "resCode": "0000",
        "msgId": "synthetic-receipt",
    }
    api._get_authenticated_headers = Mock(return_value={"Authorization": "legacy"})
    api._get_control_headers = Mock(return_value={"Authorization": "ccs2"})
    token = Token(
        username="synthetic", password="synthetic", device_id="synthetic-device"
    )
    vehicle = Vehicle(id="synthetic-car", ccu_ccs2_protocol_support=protocol)
    assert getattr(api, method)(token, vehicle, *args) == "synthetic-receipt"
    ccs2 = bool(protocol) or method == "start_hazard_lights"
    if ccs2:
        expected_payload = {"command": command}
    elif method == "stop_climate":
        expected_payload = {
            "action": "stop",
            "hvacType": 0,
            "options": {"defrost": True, "heating1": 1},
            "tempCode": "10H",
            "unit": "C",
        }
    else:
        expected_payload = {"action": command, "deviceId": "synthetic-device"}
    api.session.post.assert_called_once_with(
        (api.SPA_API_URL_V2 if ccs2 else api.SPA_API_URL)
        + "vehicles/synthetic-car/"
        + ("ccs2/" if ccs2 else "")
        + "control/"
        + endpoint,
        json=expected_payload,
        headers={"Authorization": "ccs2" if ccs2 else "legacy"},
    )
    if ccs2:
        api._get_control_headers.assert_called_once_with(token, vehicle)
        api._get_authenticated_headers.assert_not_called()
    else:
        api._get_authenticated_headers.assert_called_once_with(token, protocol)
        api._get_control_headers.assert_not_called()


def test_default_pin_grant_wire_cache_and_control_headers():
    api = ApiImplType1()
    api.USER_API_URL = "https://synthetic.invalid/api/v1/user/"
    api.BASE_URL = "synthetic.invalid"
    api.session = Mock()
    api.session.put.return_value.json.return_value = {
        "controlToken": "synthetic-control",
        "expiresTime": 60,
    }
    api._get_authenticated_headers = Mock(
        return_value={"Authorization": "access", "other": "kept"}
    )
    token = Token(
        username="synthetic",
        password="synthetic",
        access_token="access",
        device_id="device",
        pin="1234",
    )
    vehicle = Vehicle(id="synthetic-car", ccu_ccs2_protocol_support=1)
    first = api._get_control_headers(token, vehicle)
    second = api._get_control_headers(token, vehicle)
    assert (
        first
        == second
        == {
            "Authorization": "Bearer synthetic-control",
            "AuthorizationCCSP": "Bearer synthetic-control",
            "other": "kept",
        }
    )
    api.session.put.assert_called_once_with(
        "https://synthetic.invalid/api/v1/user/pin?token=",
        json={"deviceId": "device", "pin": "1234"},
        headers={
            "Authorization": "access",
            "Content-type": "application/json",
            "Host": "synthetic.invalid",
            "Accept-Encoding": "gzip",
            "User-Agent": "okhttp/3.12.0",
        },
    )
    assert api._get_control_token(token) == (
        "Bearer synthetic-control",
        token.control_token_expiry,
    )
    assert isinstance(token.control_token_expiry, int)
    assert api.session.put.call_count == 1
