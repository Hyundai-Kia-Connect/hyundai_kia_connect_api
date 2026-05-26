"""Integration tests to determine EU device_id behavior.

These experiments answer:
1. Does the EU server accept a persistent UUID as device_id (like USA/CA)?
2. Does device_id get invalidated after control commands?
3. Can we replace _get_device_id() with a stable UUID?

If persistent UUID works → Phase 3A (clean fix, eliminate notifications/register).
If server requires registered device_id → Phase 3B (retry decorator).
"""

import os
import uuid

import pytest

from hyundai_kia_connect_api.KiaUvoApiEU import KiaUvoApiEU
from hyundai_kia_connect_api.exceptions import DeviceIDError


def _get_creds(prefix: str):
    """Load credentials from env vars."""
    username = os.environ.get(f"{prefix}_USERNAME")
    password = os.environ.get(f"{prefix}_PASSWORD")
    pin = os.environ.get(f"{prefix}_PIN")
    if not all([username, password, pin]):
        pytest.skip(f"Set {prefix}_USERNAME, {prefix}_PASSWORD, {prefix}_PIN to run")
    return username, password, pin


@pytest.mark.integration
class TestDeviceIdRegistration:
    """Experiment 1: How does _get_device_id work currently?"""

    def test_device_id_is_returned_after_login(self, hyundai_token):
        """Token should have a device_id after login."""
        assert hyundai_token.device_id
        assert len(hyundai_token.device_id) > 10

    def test_device_id_stays_stable_during_reads(self, hyundai_api, hyundai_token):
        """device_id should not change between read-only API calls."""
        device_id_before = hyundai_token.device_id
        hyundai_api.get_vehicles(hyundai_token)
        assert hyundai_token.device_id == device_id_before


@pytest.mark.integration
class TestPersistentDeviceId:
    """Experiment 2: Can we use a persistent UUID like USA/CA?

    This is the key test. If it passes, we can eliminate
    _get_device_id() and notifications/register entirely.
    """

    def test_persistent_uuid_accepted_for_vehicle_list(self):
        """Replace device_id with a UUID5 and try a read-only call."""
        username, password, pin = _get_creds("HYUNDAI")
        api = KiaUvoApiEU(region=1, brand=2, language="en")
        token = api.login(username=username, password=password, pin=pin)

        original_device_id = token.device_id
        persistent_id = str(
            uuid.uuid5(uuid.NAMESPACE_DNS, f"hyundai-eu-test-{username}")
        ).upper()
        token.device_id = persistent_id

        try:
            vehicles = api.get_vehicles(token)
            assert isinstance(vehicles, list)
            print(f"PERSISTENT UUID ACCEPTED — {len(vehicles)} vehicles returned")
        except DeviceIDError:
            print("PERSISTENT UUID REJECTED — server requires registered device_id")
            raise
        except Exception as e:
            error_str = str(e)
            if (
                "4002" in error_str
                or "DeviceID" in error_str
                or "device" in error_str.lower()
            ):
                print(f"PERSISTENT UUID REJECTED — error: {error_str}")
                raise
            print(f"OTHER ERROR (not device_id): {error_str}")
            raise
        finally:
            token.device_id = original_device_id

    def test_persistent_uuid_accepted_for_cached_status(
        self, hyundai_api, hyundai_token, hyundai_vehicle
    ):
        """Replace device_id with UUID5 and try cached status read."""
        original_device_id = hyundai_token.device_id
        persistent_id = str(
            uuid.uuid5(
                uuid.NAMESPACE_DNS, f"hyundai-eu-test-{_get_creds('HYUNDAI')[0]}"
            )
        ).upper()
        hyundai_token.device_id = persistent_id

        try:
            hyundai_api.update_vehicle_with_cached_state(hyundai_token, hyundai_vehicle)
            print("PERSISTENT UUID ACCEPTED for cached status")
        except DeviceIDError:
            print("PERSISTENT UUID REJECTED for cached status")
            raise
        finally:
            hyundai_token.device_id = original_device_id


@pytest.mark.integration
class TestDeviceIdSurvivesControlCommand:
    """Experiment 3: Does device_id survive after commands?

    We use read-only operations (no actual car commands)
    to check if device_id remains valid.
    """

    def test_device_id_survives_cached_status(
        self, hyundai_api, hyundai_token, hyundai_vehicle
    ):
        """After cached status read, device_id should still work."""
        device_id_before = hyundai_token.device_id

        hyundai_api.update_vehicle_with_cached_state(hyundai_token, hyundai_vehicle)

        assert hyundai_token.device_id == device_id_before
        hyundai_api.get_vehicles(hyundai_token)

    def test_multiple_consecutive_reads(self, hyundai_api, hyundai_token):
        """Multiple rapid read calls should all work without device_id rotation."""
        device_id_initial = hyundai_token.device_id

        for _ in range(3):
            hyundai_api.get_vehicles(hyundai_token)
            assert hyundai_token.device_id == device_id_initial
