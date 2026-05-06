"""Test check_action_status polling loop behavior.

Validates:
1. _check_response_for_errors maps resCode 4004 to DuplicateRequestError
2. Non-synchronous mode propagates DuplicateRequestError (not caught there)
3. Normal polling works correctly (SUCCESS, FAILED, TIMEOUT states)
"""

import datetime as dt
from unittest.mock import patch

import pytest

from hyundai_kia_connect_api.ApiImplType1 import _check_response_for_errors
from hyundai_kia_connect_api.Vehicle import Vehicle
from hyundai_kia_connect_api.Token import Token
from hyundai_kia_connect_api.exceptions import (
    DuplicateRequestError,
)
from hyundai_kia_connect_api.VehicleManager import VehicleManager


# --- Helpers ---


def _make_eu_api():
    """Create an EU API instance for testing."""
    return VehicleManager.get_implementation_by_region_brand(1, 1, "en")


def _make_cn_api():
    """Create a CN API instance for testing."""
    return VehicleManager.get_implementation_by_region_brand(4, 1, "en")


def _make_token():
    """Create a valid token for testing."""
    return Token(
        username="test",
        password="test",
        valid_until=dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=1),
    )


def _make_vehicle():
    """Create a test vehicle."""
    v = Vehicle(id="test-vehicle-id")
    v.ccu_ccs2_protocol_support = False
    return v


def _success_response(action_id="action-123"):
    """API response for a successful action status."""
    return {
        "retCode": "S",
        "resCode": "0000",
        "resMsg": "success",
        "msgId": action_id,
    }


def _pending_response():
    """API response where action result is still None (pending)."""
    return {
        "retCode": "S",
        "resCode": "0000",
        "resMsg": [
            {"recordId": "action-123", "result": None},
        ],
    }


def _final_response(action_id="action-123", result="success"):
    """API response with a final action result."""
    return {
        "retCode": "S",
        "resCode": "0000",
        "resMsg": [
            {"recordId": action_id, "result": result},
        ],
    }


def _duplicate_request_response():
    """API response with resCode 4004 — DuplicateRequestError."""
    return {
        "retCode": "F",
        "resCode": "4004",
        "resMsg": "Duplicate request",
    }


# --- Tests: _check_response_for_errors still raises DuplicateRequestError ---


class TestCheckResponseForErrors:
    """Verify that _check_response_for_errors raises DuplicateRequestError for 4004."""

    def test_raises_duplicate_request_error(self):
        with pytest.raises(DuplicateRequestError):
            _check_response_for_errors(_duplicate_request_response())

    def test_success_response_passes(self):
        # Should not raise
        _check_response_for_errors(_success_response())


# --- Tests: check_action_status non-synchronous mode ---


class TestCheckActionStatusNonSynchronous:
    """Non-synchronous mode should still raise DuplicateRequestError."""

    @patch("hyundai_kia_connect_api.ApiImplType1.requests.get")
    def test_non_sync_raises_duplicate_request_error(self, mock_get):
        """When the API returns 4004, non-synchronous check should raise."""
        mock_get.return_value.json.return_value = _duplicate_request_response()
        api = _make_eu_api()
        token = _make_token()
        vehicle = _make_vehicle()

        with pytest.raises(DuplicateRequestError):
            api.check_action_status(token, vehicle, "action-123", synchronous=False)


# --- Tests: check_action_status synchronous loop ---


class TestCheckActionStatusSynchronousEU:
    """Test the synchronous polling loop in ApiImplType1 (EU)."""

    @patch("hyundai_kia_connect_api.ApiImplType1.requests.get")
    @patch("hyundai_kia_connect_api.ApiImplType1.sleep", return_value=None)
    def test_normal_polling_success(self, mock_sleep, mock_get):
        """Normal polling: action goes from PENDING to SUCCESS."""
        mock_get.return_value.json.side_effect = [
            _pending_response(),
            _pending_response(),
            _final_response("action-123", "success"),
        ]
        api = _make_eu_api()
        token = _make_token()
        vehicle = _make_vehicle()

        from hyundai_kia_connect_api.const import ORDER_STATUS

        result = api.check_action_status(
            token, vehicle, "action-123", synchronous=True, timeout=60
        )
        assert result == ORDER_STATUS.SUCCESS

    @patch("hyundai_kia_connect_api.ApiImplType1.requests.get")
    @patch("hyundai_kia_connect_api.ApiImplType1.sleep", return_value=None)
    def test_normal_polling_failed(self, mock_sleep, mock_get):
        """Normal polling: action goes from PENDING to FAILED."""
        mock_get.return_value.json.side_effect = [
            _pending_response(),
            _final_response("action-123", "fail"),
        ]
        api = _make_eu_api()
        token = _make_token()
        vehicle = _make_vehicle()

        from hyundai_kia_connect_api.const import ORDER_STATUS

        result = api.check_action_status(
            token, vehicle, "action-123", synchronous=True, timeout=60
        )
        assert result == ORDER_STATUS.FAILED

    @patch("hyundai_kia_connect_api.ApiImplType1.requests.get")
    @patch("hyundai_kia_connect_api.ApiImplType1.sleep", return_value=None)
    def test_timeout_returns_timeout_status(self, mock_sleep, mock_get):
        """When polling exceeds timeout, returns ORDER_STATUS.TIMEOUT.

        We simulate a timeout by making every poll return PENDING,
        then mocking the while-loop condition to exit after one iteration.
        """
        mock_get.return_value.json.return_value = _pending_response()
        api = _make_eu_api()
        token = _make_token()
        vehicle = _make_vehicle()

        from hyundai_kia_connect_api.const import ORDER_STATUS

        # Patch dt.datetime.now in the ApiImplType1 module to simulate timeout.
        # The check_action_status method uses: end_time = dt.datetime.now() + dt.timedelta(...)
        # and: while end_time > dt.datetime.now():
        # We need: first call to set end_time, second to enter the loop,
        # third to exit the loop (time exceeded).
        now = dt.datetime.now(tz=dt.timezone.utc)
        later = now + dt.timedelta(hours=1)

        with patch("hyundai_kia_connect_api.ApiImplType1.dt") as mock_dt:
            mock_dt.datetime.now.side_effect = [now, now, later]
            mock_dt.timedelta = dt.timedelta

            result = api.check_action_status(
                token, vehicle, "action-123", synchronous=True, timeout=15
            )
        assert result == ORDER_STATUS.TIMEOUT
