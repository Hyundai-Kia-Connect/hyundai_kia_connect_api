"""Tests for _retry_on_device_id_error decorator."""

import pytest

from hyundai_kia_connect_api.ApiImplType1 import _retry_on_device_id_error
from hyundai_kia_connect_api.Token import Token
from hyundai_kia_connect_api.exceptions import DeviceIDError


class TestRetryOnDeviceIdError:
    """Tests for the retry-on-DeviceIDError decorator."""

    def test_no_error_passes_through(self):
        """When no DeviceIDError, function runs once and returns result."""
        call_count = 0

        @_retry_on_device_id_error
        def mock_method(self, token):
            nonlocal call_count
            call_count += 1
            return "success"

        token = Token(access_token="at", device_id="old-device-id")
        # Simulate calling as unbound method with mock self
        mock_self = type(
            "MockApi",
            (),
            {
                "_get_device_id": lambda s, stamp: "new-device-id",
                "_get_stamp": lambda s: "stamp",
            },
        )()
        result = mock_method(mock_self, token)
        assert result == "success"
        assert call_count == 1
        # device_id unchanged — no error occurred
        assert token.device_id == "old-device-id"

    def test_device_id_error_triggers_reregister_and_retry(self):
        """On DeviceIDError, re-register device_id and retry once."""
        call_count = 0

        @_retry_on_device_id_error
        def mock_method(self, token):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise DeviceIDError("Invalid deviceId")
            return "success_after_retry"

        token = Token(access_token="at", device_id="old-device-id")
        mock_self = type(
            "MockApi",
            (),
            {
                "_get_device_id": lambda s, stamp: "new-device-id",
                "_get_stamp": lambda s: "stamp",
            },
        )()
        result = mock_method(mock_self, token)
        assert result == "success_after_retry"
        assert call_count == 2
        # device_id should be updated after re-registration
        assert token.device_id == "new-device-id"

    def test_device_id_error_on_retry_raises(self):
        """If retry also gets DeviceIDError, raise it."""
        call_count = 0

        @_retry_on_device_id_error
        def mock_method(self, token):
            nonlocal call_count
            call_count += 1
            raise DeviceIDError("Still invalid")

        token = Token(access_token="at", device_id="old-device-id")
        mock_self = type(
            "MockApi",
            (),
            {
                "_get_device_id": lambda s, stamp: "new-device-id",
                "_get_stamp": lambda s: "stamp",
            },
        )()
        with pytest.raises(DeviceIDError, match="Still invalid"):
            mock_method(mock_self, token)
        assert call_count == 2

    def test_other_exceptions_not_retried(self):
        """Non-DeviceIDError exceptions should not trigger retry."""
        call_count = 0

        @_retry_on_device_id_error
        def mock_method(self, token):
            nonlocal call_count
            call_count += 1
            raise ValueError("some other error")

        token = Token(access_token="at", device_id="old-device-id")
        mock_self = type(
            "MockApi",
            (),
            {
                "_get_device_id": lambda s, stamp: "new-device-id",
                "_get_stamp": lambda s: "stamp",
            },
        )()
        with pytest.raises(ValueError, match="some other error"):
            mock_method(mock_self, token)
        assert call_count == 1
