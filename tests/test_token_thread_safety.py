"""Tests for thread safety of device_id re-registration."""

import threading

from hyundai_kia_connect_api.ApiImplType1 import _retry_on_device_id_error
from hyundai_kia_connect_api.Token import Token
from hyundai_kia_connect_api.exceptions import DeviceIDError


class TestDeviceIdThreadSafety:
    """Tests for thread-safe device_id re-registration in the retry decorator."""

    def test_concurrent_device_id_re_registrations(self):
        """Multiple threads re-registering device_id should not corrupt state."""
        errors = []
        barrier = threading.Barrier(3)

        @_retry_on_device_id_error
        def mock_method(self, token):
            # First call always fails with DeviceIDError
            raise DeviceIDError("Invalid deviceId")

        class MockApi:
            def __init__(self):
                self._registration_count = 0
                self._lock = threading.Lock()

            def _get_device_id(self, stamp):
                with self._lock:
                    self._registration_count += 1
                    return f"device-{self._registration_count}"

            def _get_stamp(self):
                return "stamp"

        token = Token(access_token="at", device_id="initial")
        api = MockApi()

        def worker():
            try:
                barrier.wait(timeout=5)
                mock_method(api, token)
            except DeviceIDError:
                # Expected — retry also fails in this test
                errors.append(threading.current_thread().name)
            except Exception as e:
                errors.append(f"unexpected: {e}")

        threads = [threading.Thread(name=f"t{i}", target=worker) for i in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        # All threads should have attempted re-registration
        assert api._registration_count == 3
        # device_id should be one of the valid registered values
        assert token.device_id.startswith("device-")

    def test_retry_decorator_does_not_block_normal_calls(self):
        """Successful calls should not be affected by the lock."""
        call_count = 0

        @_retry_on_device_id_error
        def mock_method(self, token):
            nonlocal call_count
            call_count += 1
            return "ok"

        token = Token(access_token="at", device_id="initial")
        mock_self = type(
            "MockApi",
            (),
            {
                "_get_device_id": lambda s, stamp: "new-id",
                "_get_stamp": lambda s: "stamp",
            },
        )()

        # Multiple sequential calls should all succeed
        for _ in range(10):
            result = mock_method(mock_self, token)
            assert result == "ok"
        assert call_count == 10
