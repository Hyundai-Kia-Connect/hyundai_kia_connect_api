"""Tests for HyundaiBlueLinkApiUSA start_climate retry without seat settings."""

import pytest
from unittest.mock import MagicMock, patch

from hyundai_kia_connect_api.ApiImpl import ClimateRequestOptions
from hyundai_kia_connect_api.HyundaiBlueLinkApiUSA import HyundaiBlueLinkApiUSA
from hyundai_kia_connect_api.Token import Token
from hyundai_kia_connect_api.Vehicle import Vehicle
from hyundai_kia_connect_api.const import ENGINE_TYPES
from hyundai_kia_connect_api.exceptions import APIError


@pytest.fixture
def usa_api():
    api = HyundaiBlueLinkApiUSA(region=3, brand=2, language="en")
    return api


@pytest.fixture
def ev_vehicle_gen3():
    v = Vehicle()
    v.id = "test-vehicle-id"
    v.engine_type = ENGINE_TYPES.EV
    v.year = 2025
    v.generation = 3
    v.VIN = "TESTVIN1234567890"
    return v


@pytest.fixture
def token():
    t = Token()
    t.username = "test@example.com"
    t.access_token = "test-access-token"
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
    return opts


class TestHyundaiUSAClimateRetry:
    def test_retry_without_seats_on_failure(
        self, usa_api, token, ev_vehicle_gen3, climate_options_with_seats
    ):
        """Should retry without seat settings when first attempt fails."""
        call_count = 0

        def mock_post(url, **kwargs):
            nonlocal call_count
            call_count += 1
            resp = MagicMock()
            if call_count == 1:
                resp.status_code = 200
                resp.text = (
                    '{"errorCode":"400","errorMessage":"Invalid seat parameter"}'
                )
                resp.json.return_value = {
                    "errorCode": "400",
                    "errorMessage": "Invalid seat parameter",
                }
            else:
                resp.status_code = 200
                resp.text = ""
                resp.json.side_effect = Exception("No JSON body (success)")
            return resp

        with (
            patch.object(
                usa_api, "_get_vehicle_headers", return_value={"Authorization": "test"}
            ),
            patch.object(usa_api.sessions, "post", side_effect=mock_post),
            patch.object(usa_api, "_get_transaction_id", return_value="txn-ok"),
        ):
            usa_api.start_climate(token, ev_vehicle_gen3, climate_options_with_seats)

        assert call_count == 2

    def test_no_retry_on_success(
        self, usa_api, token, ev_vehicle_gen3, climate_options_with_seats
    ):
        """Should not retry when first attempt succeeds."""
        call_count = 0

        def mock_post(url, **kwargs):
            nonlocal call_count
            call_count += 1
            resp = MagicMock()
            resp.status_code = 200
            resp.text = ""
            resp.json.side_effect = Exception("No JSON body (success)")
            return resp

        with (
            patch.object(
                usa_api, "_get_vehicle_headers", return_value={"Authorization": "test"}
            ),
            patch.object(usa_api.sessions, "post", side_effect=mock_post),
            patch.object(usa_api, "_get_transaction_id", return_value="txn-ok"),
        ):
            usa_api.start_climate(token, ev_vehicle_gen3, climate_options_with_seats)

        assert call_count == 1

    def test_raises_error_when_both_fail(
        self, usa_api, token, ev_vehicle_gen3, climate_options_with_seats
    ):
        """Should raise APIError when both attempts fail."""

        def mock_post(url, **kwargs):
            resp = MagicMock()
            resp.status_code = 200
            resp.text = '{"errorCode":"400","errorMessage":"Invalid parameter"}'
            resp.json.return_value = {
                "errorCode": "400",
                "errorMessage": "Invalid parameter",
            }
            return resp

        with (
            patch.object(
                usa_api, "_get_vehicle_headers", return_value={"Authorization": "test"}
            ),
            patch.object(usa_api.sessions, "post", side_effect=mock_post),
        ):
            with pytest.raises(APIError):
                usa_api.start_climate(
                    token, ev_vehicle_gen3, climate_options_with_seats
                )

    def test_no_retry_on_auth_error(
        self, usa_api, token, ev_vehicle_gen3, climate_options_with_seats
    ):
        """Should not retry on authentication errors (errorCode 502)."""
        from hyundai_kia_connect_api.exceptions import AuthenticationError

        call_count = 0

        def mock_post(url, **kwargs):
            nonlocal call_count
            call_count += 1
            resp = MagicMock()
            resp.status_code = 200
            resp.text = '{"errorCode":"502","errorMessage":"Authentication invalid"}'
            resp.json.return_value = {
                "errorCode": "502",
                "errorMessage": "Authentication invalid",
            }
            return resp

        with (
            patch.object(
                usa_api, "_get_vehicle_headers", return_value={"Authorization": "test"}
            ),
            patch.object(usa_api.sessions, "post", side_effect=mock_post),
        ):
            with pytest.raises(AuthenticationError):
                usa_api.start_climate(
                    token, ev_vehicle_gen3, climate_options_with_seats
                )

        # Auth error should not trigger retry — only one call
        assert call_count == 1
