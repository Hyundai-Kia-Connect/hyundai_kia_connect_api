"""Tests for KiaUvoApiCA start_climate hvacInfo/remoteControl retry logic."""

import json

import pytest
from unittest.mock import MagicMock, patch

from hyundai_kia_connect_api.ApiImpl import ClimateRequestOptions
from hyundai_kia_connect_api.KiaUvoApiCA import KiaUvoApiCA
from hyundai_kia_connect_api.Token import Token
from hyundai_kia_connect_api.Vehicle import Vehicle
from hyundai_kia_connect_api.const import ENGINE_TYPES
from hyundai_kia_connect_api.exceptions import APIError


@pytest.fixture
def ca_api_kia():
    api = KiaUvoApiCA(region=2, brand=1, language="en")
    return api


@pytest.fixture
def ev_vehicle():
    v = Vehicle()
    v.id = "test-vehicle-id"
    v.engine_type = ENGINE_TYPES.EV
    v.year = 2024
    v.name = "EV6"
    v.model = "EV6"
    return v


@pytest.fixture
def token():
    t = Token()
    t.username = "test@example.com"
    t.access_token = "test-access-token"
    t.pin = "1234"
    t.device_id = "test-device-id"
    return t


@pytest.fixture
def climate_options():
    opts = ClimateRequestOptions()
    opts.climate = True
    opts.set_temp = 22
    opts.duration = 10
    opts.heating = 0
    opts.defrost = False
    opts.front_left_seat = 0
    opts.front_right_seat = 0
    opts.rear_left_seat = 0
    opts.rear_right_seat = 0
    return opts


class TestClimateRetry:
    def test_retry_with_remote_control_on_hvacinfo_failure(
        self, ca_api_kia, token, ev_vehicle, climate_options
    ):
        """Should retry with remoteControl key when hvacInfo payload fails."""
        call_count = 0

        def mock_post(url, **kwargs):
            nonlocal call_count
            call_count += 1
            resp = MagicMock()
            if call_count == 1:
                resp.json.return_value = {
                    "responseHeader": {"responseCode": 1},
                    "error": {
                        "errorCode": "7445",
                        "errorDesc": "Request could not be processed",
                    },
                }
                resp.status_code = 200
                resp.headers = {"transactionId": "txn-1"}
            else:
                resp.json.return_value = {
                    "responseHeader": {"responseCode": 0},
                    "result": {},
                }
                resp.status_code = 200
                resp.headers = {"transactionId": "txn-2"}
            return resp

        with (
            patch.object(ca_api_kia, "_get_pin_token", return_value="test-pauth"),
            patch.object(ca_api_kia.sessions, "post", side_effect=mock_post),
        ):
            result = ca_api_kia.start_climate(token, ev_vehicle, climate_options)

        assert call_count == 2
        assert result == "txn-2"

    def test_no_retry_on_success(self, ca_api_kia, token, ev_vehicle, climate_options):
        """Should not retry when hvacInfo payload succeeds."""
        call_count = 0

        def mock_post(url, **kwargs):
            nonlocal call_count
            call_count += 1
            resp = MagicMock()
            resp.json.return_value = {
                "responseHeader": {"responseCode": 0},
                "result": {},
            }
            resp.status_code = 200
            resp.headers = {"transactionId": "txn-ok"}
            return resp

        with (
            patch.object(ca_api_kia, "_get_pin_token", return_value="test-pauth"),
            patch.object(ca_api_kia.sessions, "post", side_effect=mock_post),
        ):
            result = ca_api_kia.start_climate(token, ev_vehicle, climate_options)

        assert call_count == 1
        assert result == "txn-ok"

    def test_raises_error_when_both_fail(
        self, ca_api_kia, token, ev_vehicle, climate_options
    ):
        """Should raise APIError when both hvacInfo and remoteControl fail."""

        def mock_post(url, **kwargs):
            resp = MagicMock()
            resp.json.return_value = {
                "responseHeader": {"responseCode": 1},
                "error": {
                    "errorCode": "7404",
                    "errorDesc": "Authentication failed",
                },
            }
            resp.status_code = 200
            resp.headers = {"transactionId": "txn-fail"}
            return resp

        with (
            patch.object(ca_api_kia, "_get_pin_token", return_value="test-pauth"),
            patch.object(ca_api_kia.sessions, "post", side_effect=mock_post),
        ):
            with pytest.raises(APIError):
                ca_api_kia.start_climate(token, ev_vehicle, climate_options)

    def test_remote_control_payload_structure(
        self, ca_api_kia, token, ev_vehicle, climate_options
    ):
        """Verify remoteControl payload has correct structure on retry."""
        captured_payloads = []

        def mock_post(url, **kwargs):
            data = kwargs.get("data", kwargs.get("json", "{}"))
            if isinstance(data, str):
                captured_payloads.append(json.loads(data))
            resp = MagicMock()
            if len(captured_payloads) == 1:
                resp.json.return_value = {
                    "responseHeader": {"responseCode": 1},
                    "error": {
                        "errorCode": "7445",
                        "errorDesc": "Bad payload",
                    },
                }
            else:
                resp.json.return_value = {
                    "responseHeader": {"responseCode": 0},
                    "result": {},
                }
            resp.status_code = 200
            resp.headers = {"transactionId": "txn-retry"}
            return resp

        with (
            patch.object(ca_api_kia, "_get_pin_token", return_value="test-pauth"),
            patch.object(ca_api_kia.sessions, "post", side_effect=mock_post),
        ):
            ca_api_kia.start_climate(token, ev_vehicle, climate_options)

        # First payload should use hvacInfo, second should use remoteControl
        assert "hvacInfo" in captured_payloads[0]
        assert "remoteControl" not in captured_payloads[0]
        assert "remoteControl" in captured_payloads[1]
        assert "hvacInfo" not in captured_payloads[1]
