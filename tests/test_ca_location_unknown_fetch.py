"""Tests for the CA location fetch gate when coordinates are unknown.

KiaUvoApiCA only calls _get_location (fndmcr, PIN-protected) when the odometer
increased since the service reading. Coordinates live only in the in-memory
Vehicle object, so after a Home Assistant restart/reload a parked car would
never get a location fetch — the device_tracker and the geocoded-location
sensor stay unavailable until the car physically moves
(Hyundai-Kia-Connect/kia_uvo#1844). The gate now also fires while no location
is known yet; once a location exists, behaviour is unchanged (moved-gate only).

_get_location is mocked at the gate boundary: an fndmcr result without a
"time" field yields coordinates with location_last_set_time None (seen in the
#1844 reporter dump) — parse_datetime(None) is None by design (kia_uvo #1771).
"""

import datetime as dt
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from hyundai_kia_connect_api.const import ENGINE_TYPES
from hyundai_kia_connect_api.KiaUvoApiCA import KiaUvoApiCA
from hyundai_kia_connect_api.Vehicle import Vehicle


@pytest.fixture
def ca_api() -> KiaUvoApiCA:
    api = KiaUvoApiCA.__new__(KiaUvoApiCA)
    api.data_timezone = KiaUvoApiCA.data_timezone
    api._get_cached_vehicle_state = MagicMock(return_value={"status": {}})
    api._update_vehicle_properties_base = MagicMock()
    api._get_next_service = MagicMock(
        return_value={"currentOdometer": 100, "currentOdometerUnit": 1}
    )
    api._update_vehicle_properties_service = MagicMock()
    api._get_forced_vehicle_state = MagicMock(
        return_value={
            "status": {
                "lastStatusDate": dt.datetime.now(dt.UTC).strftime("%Y%m%d%H%M%S")
            }
        }
    )
    api._get_location = MagicMock(
        return_value={"coord": {"lat": 43.5, "lon": -79.4}, "time": None}
    )
    return api


def _vehicle(odometer: float, with_location: bool) -> Vehicle:
    v = Vehicle()
    v.id = "vid-1"
    v.engine_type = ENGINE_TYPES.PHEV
    v._odometer = odometer
    # The real _update_vehicle_properties_base populates vehicle.data; the
    # test mocks it, so start with an empty dict like post-base state.
    v.data = {}
    if with_location:
        v.location = (43.5, -79.4, None)
    return v


def _token() -> SimpleNamespace:
    return SimpleNamespace(access_token="t", device_id="d")


class TestCachedStateLocationGate:
    def test_parked_car_without_location_fetches(self, ca_api):
        """Odometer unchanged + no location yet (post-restart) -> fetch.

        This is the kia_uvo #1844 case: before the fix the moved-gate
        suppressed the fetch and the location sensors stayed unavailable
        until the car physically moved.
        """
        vehicle = _vehicle(odometer=100, with_location=False)
        ca_api.update_vehicle_with_cached_state(_token(), vehicle)
        ca_api._get_location.assert_called_once()
        assert vehicle.location_latitude == 43.5
        assert vehicle.location_longitude == -79.4
        # fndmcr result without "time" -> timestamp stays None (HA: unknown)
        assert vehicle.location_last_updated_at is None

    def test_parked_car_with_location_keeps_moved_gate(self, ca_api):
        """Odometer unchanged + location known -> no fetch (no API load)."""
        vehicle = _vehicle(odometer=100, with_location=True)
        ca_api.update_vehicle_with_cached_state(_token(), vehicle)
        ca_api._get_location.assert_not_called()
        assert vehicle.location_latitude == 43.5
        assert vehicle.location_longitude == -79.4

    def test_moved_car_fetches(self, ca_api):
        """Odometer below the service reading -> fetch (existing behaviour)."""
        vehicle = _vehicle(odometer=99.5, with_location=True)
        ca_api.update_vehicle_with_cached_state(_token(), vehicle)
        ca_api._get_location.assert_called_once()

    def test_failed_fetch_does_not_retry_next_poll(self, ca_api):
        """A failed fndmcr attempt must not re-fire on every poll.

        _get_location swallows all errors and returns None, so coords stay
        None and the unknown-branch gate would re-run (vrfypin + fndmcr,
        two POSTs) on every poll for the whole session — the review
        concern on Hyundai-Kia-Connect/hyundai_kia_connect_api#1294.
        Bounded: one attempt per in-memory Vehicle session.
        """
        vehicle = _vehicle(odometer=100, with_location=False)
        ca_api._get_location.return_value = None  # server error path
        ca_api.update_vehicle_with_cached_state(_token(), vehicle)
        ca_api.update_vehicle_with_cached_state(_token(), vehicle)
        ca_api.update_vehicle_with_cached_state(_token(), vehicle)
        ca_api._get_location.assert_called_once()

    def test_moved_fetch_failure_does_not_retry_when_parked(self, ca_api):
        """A failed moved-gate fetch must not hand over to the unknown
        branch for an unbounded retry once the odometer catches up."""
        vehicle = _vehicle(odometer=99.5, with_location=False)
        ca_api._get_location.return_value = None
        ca_api.update_vehicle_with_cached_state(_token(), vehicle)
        vehicle._odometer = 100  # service reading catches up after the fetch
        ca_api.update_vehicle_with_cached_state(_token(), vehicle)
        ca_api._get_location.assert_called_once()

    def test_fresh_vehicle_after_restart_retries_once(self, ca_api):
        """A new Vehicle object (restart/reload) gets a fresh attempt —
        the kia_uvo #1844 recovery, not an unbounded retry."""
        ca_api._get_location.return_value = None
        vehicle = _vehicle(odometer=100, with_location=False)
        ca_api.update_vehicle_with_cached_state(_token(), vehicle)
        fresh = _vehicle(odometer=100, with_location=False)
        ca_api.update_vehicle_with_cached_state(_token(), fresh)
        assert ca_api._get_location.call_count == 2


class TestForceRefreshLocationGate:
    def test_parked_car_without_location_fetches_on_force_refresh(self, ca_api):
        """Same gate on the forced-refresh path (sibling call site)."""
        vehicle = _vehicle(odometer=100, with_location=False)
        ca_api.force_refresh_vehicle_state(_token(), vehicle)
        ca_api._get_location.assert_called_once()
        assert vehicle.location_latitude == 43.5
        assert vehicle.location_longitude == -79.4

    def test_failed_fetch_does_not_retry_on_force_refresh(self, ca_api):
        """Same bound on the forced-refresh path (sibling call site)."""
        vehicle = _vehicle(odometer=100, with_location=False)
        ca_api._get_location.return_value = None
        ca_api.force_refresh_vehicle_state(_token(), vehicle)
        ca_api.force_refresh_vehicle_state(_token(), vehicle)
        ca_api._get_location.assert_called_once()
