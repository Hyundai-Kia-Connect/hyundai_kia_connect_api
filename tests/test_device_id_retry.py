"""Tests for _retry_on_device_id_error decorator."""

from types import SimpleNamespace

import pytest

from hyundai_kia_connect_api.ApiImplType1 import ApiImplType1, _retry_on_device_id_error
from hyundai_kia_connect_api.const import ORDER_STATUS
from hyundai_kia_connect_api.exceptions import DeviceIDError
from hyundai_kia_connect_api.Token import Token


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


class TestCheckActionStatusDecorator:
    """check_action_status must be decorated with @_retry_on_device_id_error.

    On a 4002 (DeviceIDError) during the action-status poll, the decorator
    re-registers device_id and retries once. The retried poll returns empty
    resMsg (records live under the invalidated device_id), so the method
    returns ORDER_STATUS.UNKNOWN instead of propagating the exception.
    Regression for kia_uvo#1798 / hyundai_kia_connect_api#1190 gap 1.
    """

    def test_recovers_from_4002_and_reregisters_device_id(self):
        api = ApiImplType1()
        api.SPA_API_URL = "https://example.test/"
        api._get_device_id = lambda stamp: "new-device-id"
        api._get_stamp = lambda: "stamp"
        api._get_authenticated_headers = lambda token, ccu: {
            "Authorization": "Bearer x"
        }

        responses = [
            SimpleNamespace(
                json=lambda: {
                    "retCode": "F",
                    "resCode": "4002",
                    "resMsg": "Invalid request body - Invalid deviceId.",
                }
            ),
            SimpleNamespace(json=lambda: {"resCode": "0000", "resMsg": []}),
        ]
        api.session.get = lambda url, headers=None: responses.pop(0)

        token = Token(access_token="at", device_id="old-device-id")
        vehicle = SimpleNamespace(id="vid", ccu_ccs2_protocol_support=0)

        result = api.check_action_status(token, vehicle, "action-1", synchronous=False)

        assert result == ORDER_STATUS.UNKNOWN
        assert token.device_id == "new-device-id"
        assert len(responses) == 0  # both GETs consumed: initial 4002 + retry

    def test_no_error_returns_success(self):
        api = ApiImplType1()
        api.SPA_API_URL = "https://example.test/"
        api._get_device_id = lambda stamp: "new-device-id"
        api._get_stamp = lambda: "stamp"
        api._get_authenticated_headers = lambda token, ccu: {
            "Authorization": "Bearer x"
        }

        api.session.get = lambda url, headers=None: SimpleNamespace(
            json=lambda: {
                "retCode": "S",
                "resCode": "0000",
                "resMsg": [{"recordId": "action-1", "result": "success"}],
            }
        )

        token = Token(access_token="at", device_id="old-device-id")
        vehicle = SimpleNamespace(id="vid", ccu_ccs2_protocol_support=0)

        result = api.check_action_status(token, vehicle, "action-1", synchronous=False)

        assert result == ORDER_STATUS.SUCCESS
        assert token.device_id == "old-device-id"  # unchanged — no error occurred
