"""Tests for KiaUvoApiUSA start_climate retry without seat settings."""

import pytest
from unittest.mock import MagicMock, patch

from requests import RequestException

from hyundai_kia_connect_api.ApiImpl import ClimateRequestOptions
from hyundai_kia_connect_api.KiaUvoApiUSA import KiaUvoApiUSA
from hyundai_kia_connect_api.Token import Token
from hyundai_kia_connect_api.Vehicle import Vehicle
from hyundai_kia_connect_api.const import ENGINE_TYPES


@pytest.fixture
def kia_api():
    api = KiaUvoApiUSA(region=3, brand=1, language="en")
    return api


@pytest.fixture
def ev_vehicle():
    v = Vehicle()
    v.id = "test-vehicle-id"
    v.key = "test-vehicle-key"
    v.engine_type = ENGINE_TYPES.EV
    v.year = 2024
    return v


@pytest.fixture
def token():
    t = Token()
    t.username = "test@example.com"
    t.access_token = "test-sid-token"
    t.pin = "1234"
    return t


@pytest.fixture
def climate_options_with_seats():
    opts = ClimateRequestOptions()
    opts.climate = True
    opts.set_temp = 72
    opts.duration = 10
    opts.heating = 0
    opts.defrost = False
    opts.front_left_seat = 3
    opts.front_right_seat = 6
    opts.rear_left_seat = 0
    opts.rear_right_seat = 0
    opts.steering_wheel = 0
    return opts


class TestKiaUSAClimateRetry:
    def test_retry_without_seats_on_failure(
        self, kia_api, token, ev_vehicle, climate_options_with_seats
    ):
        """Should retry without seat settings when first attempt fails."""
        call_count = 0

        def mock_post_with_session(url, **kwargs):
            nonlocal call_count
            call_count += 1
            resp = MagicMock()
            if call_count == 1:
                # First call: parameter error (seat settings not supported)
                raise RequestException("Parameter error from seat settings")
            else:
                # Second call: success without seats
                resp.json.return_value = {"status": {"statusCode": 0}}
                resp.headers = {"Xid": "txn-ok"}
                return resp

        with (
            patch.object(
                kia_api,
                "post_request_with_logging_and_active_session",
                side_effect=mock_post_with_session,
            ),
        ):
            result = kia_api.start_climate(
                token, ev_vehicle, climate_options_with_seats
            )

        assert call_count == 2
        assert result == "txn-ok"

    def test_no_retry_on_success(
        self, kia_api, token, ev_vehicle, climate_options_with_seats
    ):
        """Should not retry when first attempt succeeds."""
        call_count = 0

        def mock_post_with_session(url, **kwargs):
            nonlocal call_count
            call_count += 1
            resp = MagicMock()
            resp.json.return_value = {"status": {"statusCode": 0}}
            resp.headers = {"Xid": "txn-ok"}
            return resp

        with (
            patch.object(
                kia_api,
                "post_request_with_logging_and_active_session",
                side_effect=mock_post_with_session,
            ),
        ):
            kia_api.start_climate(token, ev_vehicle, climate_options_with_seats)

        assert call_count == 1

    def test_raises_error_when_both_fail(
        self, kia_api, token, ev_vehicle, climate_options_with_seats
    ):
        """Should raise RequestException when both attempts fail."""

        def mock_post_with_session(url, **kwargs):
            raise RequestException("Parameter error from seat settings")

        with (
            patch.object(
                kia_api,
                "post_request_with_logging_and_active_session",
                side_effect=mock_post_with_session,
            ),
        ):
            with pytest.raises(RequestException):
                kia_api.start_climate(token, ev_vehicle, climate_options_with_seats)

    def test_no_retry_on_auth_error(
        self, kia_api, token, ev_vehicle, climate_options_with_seats
    ):
        """Should not retry on authentication errors."""
        from hyundai_kia_connect_api.exceptions import AuthenticationError

        call_count = 0

        def mock_post_with_session(url, **kwargs):
            nonlocal call_count
            call_count += 1
            raise AuthenticationError("Session invalid")

        with (
            patch.object(
                kia_api,
                "post_request_with_logging_and_active_session",
                side_effect=mock_post_with_session,
            ),
        ):
            with pytest.raises(AuthenticationError):
                kia_api.start_climate(token, ev_vehicle, climate_options_with_seats)

        # Auth error should not trigger seat retry — only one call
        assert call_count == 1
