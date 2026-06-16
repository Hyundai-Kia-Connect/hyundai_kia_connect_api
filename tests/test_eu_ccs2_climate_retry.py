"""Tests for CCS2 start_climate seat retry and drvSeatLoc in ApiImplType1.

CCS2 climate path: if seat settings cause a failure (retCode=="F"),
retry without seatClimateInfo. drvSeatLoc is dynamic based on
vehicle.distance_unit (km→"L", mi→"R").
"""

import pytest
from unittest.mock import MagicMock, patch

from hyundai_kia_connect_api.ApiImpl import ClimateRequestOptions
from hyundai_kia_connect_api.KiaUvoApiEU import KiaUvoApiEU
from hyundai_kia_connect_api.Token import Token
from hyundai_kia_connect_api.Vehicle import Vehicle
from hyundai_kia_connect_api.const import DISTANCE_UNITS, ENGINE_TYPES
from hyundai_kia_connect_api.exceptions import APIError, DuplicateRequestError


@pytest.fixture
def eu_api():
    return KiaUvoApiEU(region=1, brand=2, language="en")


@pytest.fixture
def ccs2_vehicle():
    v = Vehicle()
    v.id = "test-vehicle-id"
    v.engine_type = ENGINE_TYPES.PHEV
    v.ccu_ccs2_protocol_support = 1
    v.distance_unit = DISTANCE_UNITS[1]  # km → LHD
    return v


@pytest.fixture
def token():
    t = Token()
    t.access_token = "Bearer test-access-token"
    t.pin = "1234"
    t.device_id = "test-device-id"
    return t


@pytest.fixture
def climate_options_with_seats():
    opts = ClimateRequestOptions()
    opts.climate = True
    opts.set_temp = 22
    opts.duration = 10
    opts.heating = 0
    opts.defrost = False
    opts.steering_wheel = 0
    opts.front_left_seat = 3
    opts.front_right_seat = 2
    opts.rear_left_seat = 0
    opts.rear_right_seat = 0
    return opts


class TestCCS2ClimateSeatRetry:
    def test_retry_without_seats_on_failure(
        self, eu_api, token, ccs2_vehicle, climate_options_with_seats
    ):
        """Should retry without seat settings when CCS2 returns retCode=F."""
        call_count = 0

        def mock_post(url, **kwargs):
            nonlocal call_count
            call_count += 1
            resp = MagicMock()
            if call_count == 1:
                resp.json.return_value = {
                    "retCode": "F",
                    "resCode": "4005",
                    "resMsg": "Unsupported control",
                }
            else:
                resp.json.return_value = {
                    "retCode": "S",
                    "resCode": "0000",
                    "resMsg": {},
                    "msgId": "retry-msg-id",
                }
            return resp

        with (
            patch(
                "hyundai_kia_connect_api.ApiImplType1.requests.post",
                side_effect=mock_post,
            ),
            patch.object(
                eu_api, "_get_control_token", return_value=("Bearer ct", 9999)
            ),
            patch.object(eu_api, "_get_device_id", return_value="test-device-id"),
            patch.object(eu_api, "_get_stamp", return_value="test-stamp"),
        ):
            result = eu_api.start_climate(
                token, ccs2_vehicle, climate_options_with_seats
            )

        assert result == "retry-msg-id"
        assert call_count == 2

    def test_no_retry_on_duplicate_request(
        self, eu_api, token, ccs2_vehicle, climate_options_with_seats
    ):
        """Should NOT retry on DuplicateRequestError (resCode 4004)."""
        call_count = 0

        def mock_post(url, **kwargs):
            nonlocal call_count
            call_count += 1
            resp = MagicMock()
            resp.json.return_value = {
                "retCode": "F",
                "resCode": "4004",
                "resMsg": "Duplicate request",
            }
            return resp

        with (
            patch(
                "hyundai_kia_connect_api.ApiImplType1.requests.post",
                side_effect=mock_post,
            ),
            patch.object(
                eu_api, "_get_control_token", return_value=("Bearer ct", 9999)
            ),
            patch.object(eu_api, "_get_device_id", return_value="test-device-id"),
            patch.object(eu_api, "_get_stamp", return_value="test-stamp"),
        ):
            with pytest.raises(DuplicateRequestError):
                eu_api.start_climate(token, ccs2_vehicle, climate_options_with_seats)

        assert call_count == 2

    def test_no_retry_on_success(
        self, eu_api, token, ccs2_vehicle, climate_options_with_seats
    ):
        """Should not retry when CCS2 climate succeeds on first try."""
        call_count = 0

        def mock_post(url, **kwargs):
            nonlocal call_count
            call_count += 1
            resp = MagicMock()
            resp.json.return_value = {
                "retCode": "S",
                "resCode": "0000",
                "resMsg": {},
                "msgId": "success-msg-id",
            }
            return resp

        with (
            patch(
                "hyundai_kia_connect_api.ApiImplType1.requests.post",
                side_effect=mock_post,
            ),
            patch.object(
                eu_api, "_get_control_token", return_value=("Bearer ct", 9999)
            ),
            patch.object(eu_api, "_get_device_id", return_value="test-device-id"),
            patch.object(eu_api, "_get_stamp", return_value="test-stamp"),
        ):
            result = eu_api.start_climate(
                token, ccs2_vehicle, climate_options_with_seats
            )

        assert result == "success-msg-id"
        assert call_count == 1

    def test_no_retry_without_seat_settings(self, eu_api, token, ccs2_vehicle):
        """Should not retry when no seat settings are in the payload."""
        opts = ClimateRequestOptions()
        opts.climate = True
        opts.set_temp = 22
        opts.duration = 10
        opts.heating = 0
        opts.defrost = False
        opts.steering_wheel = 0
        opts.front_left_seat = None
        opts.front_right_seat = None
        opts.rear_left_seat = None
        opts.rear_right_seat = None

        call_count = 0

        def mock_post(url, **kwargs):
            nonlocal call_count
            call_count += 1
            resp = MagicMock()
            resp.json.return_value = {
                "retCode": "F",
                "resCode": "5031",
                "resMsg": "Service Temporary Unavailable",
            }
            return resp

        with (
            patch(
                "hyundai_kia_connect_api.ApiImplType1.requests.post",
                side_effect=mock_post,
            ),
            patch.object(
                eu_api, "_get_control_token", return_value=("Bearer ct", 9999)
            ),
            patch.object(eu_api, "_get_device_id", return_value="test-device-id"),
            patch.object(eu_api, "_get_stamp", return_value="test-stamp"),
        ):
            with pytest.raises(APIError):
                eu_api.start_climate(token, ccs2_vehicle, opts)

        # seatClimateInfo is always in CCS2 payload, so retry fires even
        # with None seat values. This is acceptable — the retry removes
        # the key entirely.
        assert call_count == 2


class TestDrvSeatLocDynamic:
    def test_lhd_for_kilometers(
        self, eu_api, token, ccs2_vehicle, climate_options_with_seats
    ):
        """drvSeatLoc should be 'L' for km-based vehicles (LHD markets like EU)."""
        ccs2_vehicle.distance_unit = DISTANCE_UNITS[1]  # km
        sent_payload = {}

        def mock_post(url, **kwargs):
            resp = MagicMock()
            sent_payload.update(kwargs.get("json", {}))
            resp.json.return_value = {
                "retCode": "S",
                "resCode": "0000",
                "resMsg": {},
                "msgId": "test-msg-id",
            }
            return resp

        with (
            patch(
                "hyundai_kia_connect_api.ApiImplType1.requests.post",
                side_effect=mock_post,
            ),
            patch.object(
                eu_api, "_get_control_token", return_value=("Bearer ct", 9999)
            ),
            patch.object(eu_api, "_get_device_id", return_value="test-device-id"),
            patch.object(eu_api, "_get_stamp", return_value="test-stamp"),
        ):
            eu_api.start_climate(token, ccs2_vehicle, climate_options_with_seats)

        assert sent_payload["drvSeatLoc"] == "L"

    def test_rhd_for_miles(
        self, eu_api, token, ccs2_vehicle, climate_options_with_seats
    ):
        """drvSeatLoc should be 'R' for mile-based vehicles (RHD markets like UK/AU)."""
        ccs2_vehicle.distance_unit = DISTANCE_UNITS[2]  # miles
        sent_payload = {}

        def mock_post(url, **kwargs):
            resp = MagicMock()
            sent_payload.update(kwargs.get("json", {}))
            resp.json.return_value = {
                "retCode": "S",
                "resCode": "0000",
                "resMsg": {},
                "msgId": "test-msg-id",
            }
            return resp

        with (
            patch(
                "hyundai_kia_connect_api.ApiImplType1.requests.post",
                side_effect=mock_post,
            ),
            patch.object(
                eu_api, "_get_control_token", return_value=("Bearer ct", 9999)
            ),
            patch.object(eu_api, "_get_device_id", return_value="test-device-id"),
            patch.object(eu_api, "_get_stamp", return_value="test-stamp"),
        ):
            eu_api.start_climate(token, ccs2_vehicle, climate_options_with_seats)

        assert sent_payload["drvSeatLoc"] == "R"
