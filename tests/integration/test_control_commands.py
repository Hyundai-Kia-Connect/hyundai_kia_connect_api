"""Integration tests for control commands with retry-on-device-id-error.

These tests send actual commands to the car and verify the retry
decorator works correctly when DeviceIDError occurs.

NOTE: EU server returns DuplicateRequestError for rapid consecutive
commands. Tests include delays between commands.
"""

import os
import time

import pytest

from hyundai_kia_connect_api.const import VEHICLE_LOCK_ACTION
from hyundai_kia_connect_api.exceptions import DeviceIDError

DUPLICATE_REQUEST_DELAY = 12  # seconds between control commands


def _get_creds(prefix: str):
    username = os.environ.get(f"{prefix}_USERNAME")
    password = os.environ.get(f"{prefix}_PASSWORD")
    pin = os.environ.get(f"{prefix}_PIN")
    if not all([username, password, pin]):
        pytest.skip(f"Set {prefix}_USERNAME, {prefix}_PASSWORD, {prefix}_PIN to run")
    return username, password, pin


@pytest.mark.integration
class TestLockActionWithRetry:
    """Test lock/unlock with device_id retry behavior."""

    def test_lock_action_returns_msg_id(
        self, hyundai_api, hyundai_token, hyundai_vehicle
    ):
        """Lock command should return a msgId (string action ID)."""
        result = hyundai_api.lock_action(
            hyundai_token, hyundai_vehicle, VEHICLE_LOCK_ACTION.LOCK
        )
        assert isinstance(result, str)
        assert len(result) > 0
        print(f"Lock action msgId: {result}")

    def test_unlock_after_delay(self, hyundai_api, hyundai_token, hyundai_vehicle):
        """Unlock after delay should return a msgId."""
        time.sleep(DUPLICATE_REQUEST_DELAY)
        result = hyundai_api.lock_action(
            hyundai_token, hyundai_vehicle, VEHICLE_LOCK_ACTION.UNLOCK
        )
        # DuplicateRequestError is acceptable — EU server rate-limits
        if isinstance(result, str):
            assert len(result) > 0
            print(f"Unlock action msgId: {result}")
        else:
            print(f"Unlock returned: {result}")


@pytest.mark.integration
class TestDeviceIdBehavior:
    """Test device_id stability and retry behavior."""

    def test_device_id_stable_after_read(
        self, hyundai_api, hyundai_token, hyundai_vehicle
    ):
        """device_id should not change during read-only operations."""
        device_id_before = hyundai_token.device_id
        hyundai_api.update_vehicle_with_cached_state(hyundai_token, hyundai_vehicle)
        assert hyundai_token.device_id == device_id_before

    def test_retry_on_invalid_device_id_during_read(
        self, hyundai_api, hyundai_token, hyundai_vehicle
    ):
        """Replace device_id with invalid value — retry should recover for reads.

        The decorator should catch DeviceIDError, re-register device_id,
        and retry the read successfully.
        """
        original_device_id = hyundai_token.device_id

        # Temporarily set an invalid device_id to trigger 4002
        hyundai_token.device_id = "invalid-device-id-will-fail"

        try:
            # This should trigger DeviceIDError → retry → success
            hyundai_api.update_vehicle_with_cached_state(hyundai_token, hyundai_vehicle)
            # If we get here, retry worked — device_id was re-registered
            assert hyundai_token.device_id != "invalid-device-id-will-fail"
            print(f"Retry succeeded — new device_id: {hyundai_token.device_id}")
        except DeviceIDError:
            print("DeviceIDError persisted even after retry")
            hyundai_token.device_id = original_device_id
            raise
        finally:
            if hyundai_token.device_id == "invalid-device-id-will-fail":
                hyundai_token.device_id = original_device_id
