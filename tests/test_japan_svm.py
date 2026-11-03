"""Synthetic, offline Japan request-correlated camera contracts."""

import base64
import datetime as dt
from unittest.mock import Mock
from zoneinfo import ZoneInfo

import pytest

from hyundai_kia_connect_api.exceptions import (
    AuthenticationError,
    InvalidAPIResponseError,
    NoDataFound,
    RequestTimeoutError,
    SafetyAcknowledgmentError,
    UnsupportedControlError,
)
from hyundai_kia_connect_api.japan_svm import JapanSVM, parse_japan_svm
from hyundai_kia_connect_api.Vehicle import Vehicle


def payload():
    return {
        "scsDetail": {
            "svmImage": base64.b64encode(b"SYNTHETIC_IMAGE").decode(),
            "gpsDetail": {"time": "20261014120000", "coord": {"lat": 35, "lon": 139}},
            "imageSize": [100, 200, 10, 20],
            "validAngleofView": [1.0, 2.0],
            "doorOpen": {"frontLeft": 1},
            "trunkOpen": False,
        }
    }


@pytest.fixture
def adapter():
    api = JapanSVM()
    api.data_timezone = ZoneInfo("Asia/Tokyo")
    api._headers = Mock(return_value={"Authorization": "SYNTHETIC_TOKEN"})
    api._control_headers = Mock(
        return_value={"Authorization": "SYNTHETIC_CONTROL_TOKEN"}
    )
    api._vehicle_headers = Mock(
        return_value={
            "Authorization": "SYNTHETIC_OAUTH_TOKEN",
            "vehicleId": "CAR",
            "ccuCCS2ProtocolSupport": "0",
        }
    )
    api._payload = lambda response: response["resMsg"]
    api._request = Mock()
    return api


def test_parse_and_redact():
    result = parse_japan_svm(payload(), ZoneInfo("Asia/Tokyo"))
    assert result.image_bytes == b"SYNTHETIC_IMAGE"
    assert result.captured_at == dt.datetime(
        2026, 10, 14, 12, tzinfo=ZoneInfo("Asia/Tokyo")
    )
    assert result.latitude == 35
    assert result.longitude == 139
    assert result.image_sizes == (100, 200, 10, 20)
    assert result.door_open["frontLeft"] is True
    assert result.raw_metadata["scsDetail"]["svmImage"] == "<redacted>"


@pytest.mark.parametrize(
    "value",
    [
        {},
        {"scsDetail": {}},
        {"scsDetail": {"svmImage": "!"}},
        {"scsDetail": {"svmImage": ""}},
    ],
)
def test_invalid_image(value):
    with pytest.raises(InvalidAPIResponseError):
        parse_japan_svm(value, ZoneInfo("Asia/Tokyo"))


def test_passive_read_does_not_trigger_capture(adapter):
    with pytest.raises(NoDataFound):
        adapter.get_svm_details(None, Vehicle(id="CAR"))
    adapter._request.assert_not_called()


def test_warning_and_profile_guard_before_network(adapter):
    vehicle = Vehicle(id="CAR", supports_svm=False)
    with pytest.raises(SafetyAcknowledgmentError):
        adapter.request_svm_capture(None, vehicle)
    with pytest.raises(UnsupportedControlError):
        adapter.request_svm_capture(None, vehicle, True)
    adapter._request.assert_not_called()


def test_poll_is_correlated_and_only_retries_pending(adapter, monkeypatch):
    monkeypatch.setattr(
        "hyundai_kia_connect_api.japan_svm.time.sleep", lambda seconds: None
    )
    adapter._request.side_effect = [
        {"msgId": "SYNTHETIC_ID"},
        NoDataFound("pending"),
        {"resMsg": payload()},
    ]
    vehicle = Vehicle(id="CAR", supports_svm=True)
    result = adapter.request_svm_capture(None, vehicle, True)
    assert adapter._request.call_count == 3
    calls = adapter._request.call_args_list
    assert calls[0].args == ("POST", "/api/v1/spa/vehicles/CAR/svm/async")
    adapter._control_headers.assert_called_once_with(None, vehicle)
    assert all(
        call.kwargs["headers"]["Authorization"] == "SYNTHETIC_OAUTH_TOKEN"
        for call in calls
    )
    assert all(call.kwargs["headers"]["vehicleId"] == "CAR" for call in calls)
    assert all(call.kwargs["params"] == {"msgId": "SYNTHETIC_ID"} for call in calls[1:])
    assert adapter.get_svm_details(None, vehicle) is result
    assert adapter._request.call_count == 3


def test_pin_precondition_failure_never_submits_or_polls_camera(adapter):
    adapter._control_headers.side_effect = AuthenticationError("PIN rejected")
    with pytest.raises(AuthenticationError):
        adapter.request_svm_capture(None, Vehicle(id="CAR", supports_svm=True), True)
    adapter._request.assert_not_called()
    adapter._vehicle_headers.assert_not_called()


def test_pin_precondition_precedes_camera_oauth_context_and_post(adapter):
    order = []
    adapter._control_headers.side_effect = lambda token, vehicle: (
        order.append("PIN") or {"Authorization": "CONTROL"}
    )
    adapter._vehicle_headers.side_effect = lambda token, vehicle: (
        order.append("OAuth context") or {"Authorization": "OAUTH"}
    )

    def request(method, path, **kwargs):
        order.append(method)
        return {"msgId": "SYNTHETIC_ID"} if method == "POST" else {"resMsg": payload()}

    adapter._request.side_effect = request
    adapter.request_svm_capture(None, Vehicle(id="CAR", supports_svm=True), True)
    assert order == ["PIN", "OAuth context", "POST", "OAuth context", "GET"]


def test_ack_without_image_is_not_success(adapter):
    adapter._request.side_effect = [{"msgId": "SYNTHETIC_ID"}, {"resMsg": {}}]
    with pytest.raises(InvalidAPIResponseError):
        adapter.request_svm_capture(None, Vehicle(id="CAR", supports_svm=True), True)
    with pytest.raises(NoDataFound):
        adapter.get_svm_details(None, Vehicle(id="CAR"))


def test_timeout_and_rejection_do_not_repeat_capture(adapter, monkeypatch):
    monkeypatch.setattr(
        "hyundai_kia_connect_api.japan_svm.time.monotonic",
        Mock(side_effect=[0, 0, 146, 146]),
    )
    adapter._request.side_effect = [{"msgId": "SYNTHETIC_ID"}, NoDataFound("pending")]
    with pytest.raises(RequestTimeoutError):
        adapter.request_svm_capture(None, Vehicle(id="CAR", supports_svm=True), True)
    assert adapter._request.call_count == 2
    assert adapter.svm_diagnostics["stage"] == "failed"
    assert adapter.svm_diagnostics["failed_stage"] == "polling"
    assert adapter.svm_diagnostics["failure_category"] == "timeout"
    assert adapter.svm_diagnostics["poll_count"] == 1


def test_slow_vehicle_upload_within_app_polling_window_is_displayable(
    adapter, monkeypatch
):
    clock = [0]
    monkeypatch.setattr(
        "hyundai_kia_connect_api.japan_svm.time.monotonic", lambda: clock[0]
    )

    def sleep(seconds):
        clock[0] += seconds

    monkeypatch.setattr("hyundai_kia_connect_api.japan_svm.time.sleep", sleep)

    def request(method, path, **kwargs):
        if method == "POST":
            return {"msgId": "SYNTHETIC_ID"}
        assert kwargs["params"] == {"msgId": "SYNTHETIC_ID"}
        if clock[0] < 130:
            raise NoDataFound("pending")
        return {"resMsg": payload()}

    adapter._request.side_effect = request
    vehicle = Vehicle(id="CAR", supports_svm=True)
    details = adapter.request_svm_capture(None, vehicle, True)
    assert clock[0] == 130
    assert adapter.get_svm_details(None, vehicle) is details
    assert sum(call.args[0] == "POST" for call in adapter._request.call_args_list) == 1


def test_invalid_identifier_before_request(adapter):
    with pytest.raises(InvalidAPIResponseError):
        adapter.request_svm_capture(None, Vehicle(id="../CAR", supports_svm=True), True)
    adapter._request.assert_not_called()


def test_workflow_diagnostics_track_pending_and_success_without_private_data(
    adapter, monkeypatch
):
    monkeypatch.setattr("hyundai_kia_connect_api.japan_svm.time.sleep", lambda _: None)
    observed = []

    def request(method, path, **kwargs):
        observed.append(adapter.svm_diagnostics)
        if method == "POST":
            return {"msgId": "PRIVATE_MESSAGE_ID"}
        if len(observed) == 2:
            raise NoDataFound("PRIVATE_VENDOR_MESSAGE")
        return {"resMsg": payload()}

    adapter._request.side_effect = request
    adapter.request_svm_capture(
        None, Vehicle(id="PRIVATE_CAR", supports_svm=True), True
    )
    summary = adapter.svm_diagnostics
    assert [item["stage"] for item in observed] == ["submission", "polling", "polling"]
    assert summary["stage"] == "complete"
    assert summary["poll_count"] == 2
    assert summary["image_present"] is True
    assert summary["dimensions_present"] is True
    assert summary["elapsed_seconds"] >= 0
    assert summary["failure_category"] is None
    assert "PRIVATE" not in repr(summary)
    assert "SYNTHETIC_IMAGE" not in repr(summary)
    summary["stage"] = "modified"
    assert adapter.svm_diagnostics["stage"] == "complete"


@pytest.mark.parametrize(
    ("failure", "category"),
    [
        (AuthenticationError("PRIVATE_VENDOR_MESSAGE"), "authentication"),
        (RuntimeError("PRIVATE_TOKEN"), "unexpected"),
    ],
)
def test_workflow_diagnostics_preserve_safe_failure(adapter, failure, category):
    adapter._control_headers.side_effect = failure
    with pytest.raises(type(failure)):
        adapter.request_svm_capture(
            None, Vehicle(id="PRIVATE_CAR", supports_svm=True), True
        )
    summary = adapter.svm_diagnostics
    assert summary["stage"] == "failed"
    assert summary["failed_stage"] == "pin"
    assert summary["failure_category"] == category
    assert summary["poll_count"] == 0
    assert summary["image_present"] is False
    assert summary["dimensions_present"] is False
    assert summary["elapsed_seconds"] >= 0
    assert "PRIVATE" not in repr(summary)
    assert adapter.svm_diagnostics == summary


@pytest.mark.parametrize("value", [None, [], "PRIVATE_RESPONSE", 42])
def test_non_object_camera_payload_is_sanitized(value):
    with pytest.raises(
        InvalidAPIResponseError, match="response is incomplete"
    ) as error:
        parse_japan_svm(value, ZoneInfo("Asia/Tokyo"))
    assert "PRIVATE" not in str(error.value)


def test_non_finite_image_dimension_is_sanitized():
    response = payload()
    response["scsDetail"]["imageSize"] = [float("inf"), 720]
    with pytest.raises(InvalidAPIResponseError, match="dimensions are invalid"):
        parse_japan_svm(response, ZoneInfo("Asia/Tokyo"))


def test_line_wrapped_base64_is_accepted_but_unknown_characters_are_rejected():
    response = payload()
    encoded = response["scsDetail"]["svmImage"]
    response["scsDetail"]["svmImage"] = " \t\r\n".join(
        encoded[index : index + 4] for index in range(0, len(encoded), 4)
    )
    assert (
        parse_japan_svm(response, ZoneInfo("Asia/Tokyo")).image_bytes
        == b"SYNTHETIC_IMAGE"
    )
    response["scsDetail"]["svmImage"] += "\u200b"
    with pytest.raises(InvalidAPIResponseError, match="image is invalid"):
        parse_japan_svm(response, ZoneInfo("Asia/Tokyo"))
