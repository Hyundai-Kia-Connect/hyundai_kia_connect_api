"""Tests for HyundaiBlueLinkApiUSA start_climate retry without seat settings.

Response-level check pattern: retry is triggered by the presence of
``errorCode`` in the response JSON, not by the exception type. This means
errorCode 502 (which maps to AuthenticationError) also triggers retry if
seat settings are present. The second call either succeeds or raises the
appropriate exception.
"""

import pytest
from unittest.mock import MagicMock, patch

from hyundai_kia_connect_api.ApiImpl import ClimateRequestOptions
from hyundai_kia_connect_api.HyundaiBlueLinkApiUSA import HyundaiBlueLinkApiUSA
from hyundai_kia_connect_api.Token import Token
from hyundai_kia_connect_api.Vehicle import Vehicle
from hyundai_kia_connect_api.const import ENGINE_TYPES
from hyundai_kia_connect_api.exceptions import APIError, AuthenticationError


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
    def test_retry_without_seats_on_parameter_error(
        self, usa_api, token, ev_vehicle_gen3, climate_options_with_seats
    ):
        """Should retry without seat settings when server returns a parameter errorCode."""
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

    def test_retry_without_seats_on_errorcode_502(
        self, usa_api, token, ev_vehicle_gen3, climate_options_with_seats
    ):
        """Should retry even when errorCode is 502 (response-level check, not exception-based).

        If 502 turns out to be a parameter error (not auth), the retry without
        seats may succeed. If 502 is truly auth, the second call will also get
        502 and raise AuthenticationError — one extra API call, no harm.
        """
        call_count = 0

        def mock_post(url, **kwargs):
            nonlocal call_count
            call_count += 1
            resp = MagicMock()
            if call_count == 1:
                resp.status_code = 200
                resp.text = '{"errorCode":"502","errorMessage":"Invalid parameter"}'
                resp.json.return_value = {
                    "errorCode": "502",
                    "errorMessage": "Invalid parameter",
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
        """Should not retry when first attempt succeeds (empty body = success)."""
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

    def test_raises_apierror_when_both_fail(
        self, usa_api, token, ev_vehicle_gen3, climate_options_with_seats
    ):
        """Should raise APIError when both attempts fail with a non-502 errorCode."""

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

    def test_raises_autherror_when_both_fail_with_502(
        self, usa_api, token, ev_vehicle_gen3, climate_options_with_seats
    ):
        """Should raise AuthenticationError when both attempts return errorCode 502.

        The retry happens (response-level check), but since both calls return 502,
        the final _check_response_for_errors raises AuthenticationError.
        """
        call_count = 0

        def mock_post(url, **kwargs):
            nonlocal call_count
            call_count += 1
            resp = MagicMock()
            resp.status_code = 200
            resp.text = '{"errorCode":"502","errorMessage":"Auth invalid"}'
            resp.json.return_value = {
                "errorCode": "502",
                "errorMessage": "Auth invalid",
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

        # Response-level check triggers retry even on 502 — both calls are made
        assert call_count == 2

    def test_no_retry_without_seat_settings(self, usa_api, token):
        """Should not retry when error occurs but no seat settings in payload.

        Gen 2 vehicles (generation != 3) don't include seatHeaterVentInfo,
        so there's nothing to remove and retry.
        """
        gen2_vehicle = Vehicle()
        gen2_vehicle.id = "test-vehicle-id"
        gen2_vehicle.engine_type = ENGINE_TYPES.EV
        gen2_vehicle.year = 2022
        gen2_vehicle.generation = 2
        gen2_vehicle.VIN = "TESTVIN1234567890"

        opts = ClimateRequestOptions()
        opts.climate = True
        opts.set_temp = 72
        opts.duration = 10
        opts.heating = 0
        opts.defrost = False
        opts.front_left_seat = None
        opts.front_right_seat = None
        opts.rear_left_seat = None
        opts.rear_right_seat = None

        call_count = 0

        def mock_post(url, **kwargs):
            nonlocal call_count
            call_count += 1
            resp = MagicMock()
            resp.status_code = 200
            resp.text = '{"errorCode":"400","errorMessage":"Some error"}'
            resp.json.return_value = {
                "errorCode": "400",
                "errorMessage": "Some error",
            }
            return resp

        with (
            patch.object(
                usa_api, "_get_vehicle_headers", return_value={"Authorization": "test"}
            ),
            patch.object(usa_api.sessions, "post", side_effect=mock_post),
        ):
            with pytest.raises(APIError):
                usa_api.start_climate(token, gen2_vehicle, opts)

        # No seat settings in payload → no retry
        assert call_count == 1
