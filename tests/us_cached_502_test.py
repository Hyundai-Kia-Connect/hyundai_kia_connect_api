"""Tests for HyundaiBlueLinkApiUSA cached status 502 resilience.

When the HATA backend returns 502 on cached vehicle status (REFRESH: false),
update_vehicle_with_cached_state should log a warning and keep existing
vehicle data rather than propagating the exception and making all entities
unavailable.
"""

import datetime as dt
from unittest.mock import MagicMock, patch

import pytest

from hyundai_kia_connect_api.HyundaiBlueLinkApiUSA import HyundaiBlueLinkApiUSA
from hyundai_kia_connect_api.Token import Token
from hyundai_kia_connect_api.Vehicle import Vehicle
from hyundai_kia_connect_api.exceptions import APIError, AuthenticationError


def _make_token():
    return Token(
        username="test@user.com",
        password="pass",
        access_token="at",
        refresh_token="rt",
        valid_until=None,
        device_id="dev",
        pin="1234",
    )


def _make_vehicle():
    v = Vehicle()
    v.id = "test-vehicle-id"
    v.name = "Test Genesis"
    v.enabled = True
    return v


def _make_api():
    return HyundaiBlueLinkApiUSA.__new__(HyundaiBlueLinkApiUSA)


class TestCachedStatus502Resilience:
    def test_cached_502_does_not_propagate(self):
        """Cached vehicle status returning 502 should not raise."""
        api = _make_api()
        token = _make_token()
        vehicle = _make_vehicle()

        api._get_vehicle_details = MagicMock(return_value={"odometer": 1000})
        api._get_vehicle_status = MagicMock(
            side_effect=APIError("API Error 502: server error")
        )

        api.update_vehicle_with_cached_state(token, vehicle)

    def test_cached_502_keeps_existing_vehicle_data(self):
        """When cached status fails, vehicle keeps its existing data."""
        api = _make_api()
        token = _make_token()
        vehicle = _make_vehicle()
        vehicle._total_driving_range = 100.0
        vehicle._total_driving_range_value = 100.0
        vehicle._total_driving_range_unit = "km"

        api._get_vehicle_details = MagicMock(return_value={"odometer": 1000})
        api._get_vehicle_status = MagicMock(
            side_effect=APIError("API Error 502: server error")
        )

        api.update_vehicle_with_cached_state(token, vehicle)

        # Vehicle data should be unchanged
        assert vehicle.total_driving_range == 100.0

    def test_cached_success_still_updates(self):
        """When cached status succeeds, vehicle is updated normally."""
        api = _make_api()
        token = _make_token()
        vehicle = _make_vehicle()

        api._get_vehicle_details = MagicMock(return_value={"odometer": 1000})
        api._get_vehicle_status = MagicMock(
            return_value={"vehicleStatus": {"doorLock": True}}
        )
        api._get_ev_trip_details = MagicMock(return_value=None)
        api._get_vehicle_location = MagicMock(return_value=None)
        api._update_vehicle_properties = MagicMock()

        api.update_vehicle_with_cached_state(token, vehicle)

        api._update_vehicle_properties.assert_called_once()

    def test_cached_auth_error_does_propagate(self):
        """Non-server errors (like AuthenticationError) should still propagate."""
        api = _make_api()
        token = _make_token()
        vehicle = _make_vehicle()

        api._get_vehicle_details = MagicMock(return_value={"odometer": 1000})
        api._get_vehicle_status = MagicMock(
            side_effect=AuthenticationError("Auth failed")
        )

        with pytest.raises(AuthenticationError):
            api.update_vehicle_with_cached_state(token, vehicle)


class TestCheckAndForceUpdateVehicleNoData:
    """When last_updated_at is None, force refresh should be used."""

    def test_no_data_uses_force_refresh(self):
        from hyundai_kia_connect_api.VehicleManager import VehicleManager

        mgr = VehicleManager.__new__(VehicleManager)
        vehicle = _make_vehicle()
        mgr.vehicles = {vehicle.id: vehicle}

        assert vehicle.last_updated_at is None

        with (
            patch.object(mgr, "force_refresh_vehicle_state") as mock_force,
            patch.object(mgr, "update_vehicle_with_cached_state") as mock_cached,
        ):
            mgr.check_and_force_update_vehicle(1440, vehicle.id)

            mock_force.assert_called_once_with(vehicle.id)
            mock_cached.assert_not_called()

    def test_has_data_within_interval_uses_cached(self):
        from hyundai_kia_connect_api.VehicleManager import VehicleManager

        mgr = VehicleManager.__new__(VehicleManager)
        vehicle = _make_vehicle()
        vehicle.last_updated_at = dt.datetime.now(dt.timezone.utc) - dt.timedelta(
            minutes=30
        )
        mgr.vehicles = {vehicle.id: vehicle}

        with (
            patch.object(mgr, "force_refresh_vehicle_state") as mock_force,
            patch.object(mgr, "update_vehicle_with_cached_state") as mock_cached,
        ):
            mgr.check_and_force_update_vehicle(7200, vehicle.id)

            mock_cached.assert_called_once_with(vehicle.id)
            mock_force.assert_not_called()

    def test_has_data_past_interval_uses_force_refresh(self):
        from hyundai_kia_connect_api.VehicleManager import VehicleManager

        mgr = VehicleManager.__new__(VehicleManager)
        vehicle = _make_vehicle()
        vehicle.last_updated_at = dt.datetime.now(dt.timezone.utc) - dt.timedelta(
            hours=25
        )
        mgr.vehicles = {vehicle.id: vehicle}

        with (
            patch.object(mgr, "force_refresh_vehicle_state") as mock_force,
            patch.object(mgr, "update_vehicle_with_cached_state") as mock_cached,
        ):
            mgr.check_and_force_update_vehicle(1440, vehicle.id)

            mock_force.assert_called_once_with(vehicle.id)
            mock_cached.assert_not_called()
