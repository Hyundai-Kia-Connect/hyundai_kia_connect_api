"""Replay app-derived HTTP acceptance contracts without a vehicle connection."""

import json
from pathlib import Path
from unittest.mock import Mock

import pytest
import requests

from hyundai_kia_connect_api.const import ORDER_STATUS, VEHICLE_LOCK_ACTION
from hyundai_kia_connect_api.exceptions import (
    APIError,
    AuthenticationError,
    InvalidAPIResponseError,
    RequestTimeoutError,
)
from hyundai_kia_connect_api.HyundaiBlueLinkApiJP import HyundaiBlueLinkApiJP
from hyundai_kia_connect_api.Token import Token
from hyundai_kia_connect_api.Vehicle import Vehicle

CONTRACT = json.loads(
    (
        Path(__file__).parent / "fixtures/japan/jp_control_completion_contract.json"
    ).read_text()
)


def response(body, status=200):
    result = Mock(status_code=status)
    result.json.return_value = body
    return result


@pytest.fixture
def setup():
    api = HyundaiBlueLinkApiJP(10, 2, "en")
    api.session = Mock()
    helper = Mock()
    helper.request_context = {
        "clientId": "synthetic-client",
        "vehicleId": "synthetic-broker",
    }
    helper.track.side_effect = AssertionError(
        "ordinary controls must not register MQTT result waits"
    )
    helper.status.side_effect = AssertionError(
        "ordinary controls must not wait for MQTT completion"
    )
    api._mqtt_for_vehicle = Mock(return_value=helper)
    token = Token(
        username="synthetic",
        password="synthetic",
        access_token="Bearer synthetic-access",
        device_id="synthetic-device",
        pin="synthetic-pin",
        mqtt_registration_uuid="synthetic-registration",
    )
    vehicle = Vehicle(id="synthetic-car", ccu_ccs2_protocol_support=1)
    return api, helper, token, vehicle


@pytest.mark.parametrize(
    "endpoint",
    CONTRACT["ordinary_control"]["endpoints"],
    ids=lambda endpoint: endpoint["suffix"],
)
def test_ordinary_app_endpoints_complete_with_service_acceptance(setup, endpoint):
    api, helper, token, vehicle = setup
    api.session.request.side_effect = [
        response({"controlToken": "synthetic-control", "expiresTime": 60}),
        response(CONTRACT["ordinary_control"]["known_success_envelope"]),
    ]
    action = api._japan_command(
        token, vehicle, endpoint["suffix"], {}, ccs2=endpoint["ccs2"]
    )
    assert action
    assert (
        api.check_action_status(token, vehicle, action, True, 60)
        == ORDER_STATUS.ACCEPTED
    )
    assert api.check_action_status(token, vehicle, action) != ORDER_STATUS.SUCCESS
    sent = api.session.request.call_args
    prefix = "/ccs2/" if endpoint["ccs2"] else "/"
    assert sent.args[1].endswith(prefix + endpoint["suffix"])
    assert sent.kwargs["timeout"] == (
        10,
        CONTRACT["ordinary_control"]["read_timeout_seconds"],
    )
    assert sent.kwargs["headers"]["clientId"] == "synthetic-client"
    assert sent.kwargs["headers"]["vehicleId"] == "synthetic-broker"
    helper.track.assert_not_called()
    helper.status.assert_not_called()
    helper.close.assert_not_called()
    assert api._japan_command_diagnostics["stage"] == "accepted"
    assert api._japan_command_diagnostics["confirmation_source"] == "http"
    assert api._japan_command_diagnostics["vehicle_execution_confirmed"] is False


@pytest.mark.parametrize(
    "body", [{}, {"retCode": "S"}, {"retCode": "S", "msgId": "synthetic-ack"}]
)
def test_msg_id_is_optional_for_acceptance_but_never_physical_confirmation(setup, body):
    api, helper, token, vehicle = setup
    api.session.request.side_effect = [
        response({"controlToken": "synthetic-control", "expiresTime": 60}),
        response(body),
    ]
    action = api.lock_action(token, vehicle, VEHICLE_LOCK_ACTION.LOCK)
    assert api.check_action_status(token, vehicle, action) == ORDER_STATUS.ACCEPTED
    helper.status.assert_not_called()


@pytest.mark.parametrize(
    "status,body,error",
    [
        (
            200,
            {
                "retCode": "F",
                "resCode": "9999",
                "resultDescription": "SYNTHETIC_PRIVATE",
            },
            APIError,
        ),
        (200, {"retCode": "S", "resCode": "9999"}, APIError),
        (403, {}, AuthenticationError),
        (500, {}, APIError),
        (200, [], InvalidAPIResponseError),
    ],
)
def test_http_and_vendor_errors_never_create_acceptance_receipts(
    setup, status, body, error
):
    api, helper, token, vehicle = setup
    api.session.request.side_effect = [
        response({"controlToken": "synthetic-control", "expiresTime": 60}),
        response(body, status),
    ]
    with pytest.raises(error):
        api.lock_action(token, vehicle, VEHICLE_LOCK_ACTION.UNLOCK)
    assert getattr(api, "_japan_actions", {}) == {}
    assert api._japan_command_diagnostics["stage"] == "failed"
    assert "SYNTHETIC_PRIVATE" not in json.dumps(api._japan_command_diagnostics)
    helper.track.assert_not_called()
    helper.close.assert_not_called()


def test_http_timeout_has_unknown_submission_outcome_and_is_not_retried(setup):
    api, helper, token, vehicle = setup
    api.session.request.side_effect = [
        response({"controlToken": "synthetic-control", "expiresTime": 60}),
        requests.exceptions.Timeout("SYNTHETIC_PRIVATE_URL"),
    ]
    with pytest.raises(RequestTimeoutError):
        api.lock_action(token, vehicle, VEHICLE_LOCK_ACTION.UNLOCK)
    assert api.session.request.call_count == 2
    assert getattr(api, "_japan_actions", {}) == {}
    assert api._japan_command_diagnostics["stage"] == "submission_unknown"
    assert api._japan_command_diagnostics["failed_stage"] == "http_submitting"
    assert api._japan_command_diagnostics["failure_category"] == "timeout"
    assert api._japan_command_diagnostics["vehicle_execution_confirmed"] is False
    assert "SYNTHETIC_PRIVATE" not in json.dumps(api._japan_command_diagnostics)
    helper.track.assert_not_called()
    helper.close.assert_not_called()


@pytest.mark.parametrize(
    "states,expected",
    [
        ([ORDER_STATUS.ACCEPTED, ORDER_STATUS.SUCCESS], ORDER_STATUS.ACCEPTED),
        ([ORDER_STATUS.ACCEPTED, ORDER_STATUS.ACCEPTED], ORDER_STATUS.ACCEPTED),
        ([ORDER_STATUS.SUCCESS, ORDER_STATUS.SUCCESS], ORDER_STATUS.SUCCESS),
    ],
)
def test_composite_receipts_preserve_acceptance_distinction(setup, states, expected):
    api, helper, token, vehicle = setup
    for i, state in enumerate(states):
        api._remember_japan_action(str(i), vehicle.id, status=state)
    api._remember_japan_action("combined", vehicle.id, ("0", "1"))
    assert api.check_action_status(token, vehicle, "combined", True, 60) == expected
    helper.status.assert_not_called()
    helper.close.assert_not_called()


@pytest.mark.parametrize("marker", ["retCode", "resultType"])
def test_lowercase_vendor_failure_is_rejected_even_with_success_shaped_code(
    setup, marker
):
    api, helper, token, vehicle = setup
    api.session.request.side_effect = [
        response({"controlToken": "synthetic-control", "expiresTime": 60}),
        response({marker: "f", "resCode": "0000"}),
    ]
    with pytest.raises(APIError):
        api.lock_action(token, vehicle, VEHICLE_LOCK_ACTION.UNLOCK)
    assert getattr(api, "_japan_actions", {}) == {}
    assert api._japan_command_diagnostics["stage"] == "failed"
    helper.track.assert_not_called()
    helper.status.assert_not_called()
