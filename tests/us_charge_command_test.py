"""Tests for start_charge and stop_charge commands in HyundaiBlueLinkApiUSA.

Covers the fix for JSONDecodeError when Hyundai's API returns an empty
response body (HTTP 200 OK, no body) for charge commands on newer vehicles
such as the 2026 Hyundai Ioniq 9.
"""

import datetime as dt
import logging
from unittest.mock import MagicMock, patch


from hyundai_kia_connect_api.const import ENGINE_TYPES
from hyundai_kia_connect_api.HyundaiBlueLinkApiUSA import HyundaiBlueLinkApiUSA
from hyundai_kia_connect_api.Token import Token
from hyundai_kia_connect_api.Vehicle import Vehicle


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeResponse:
    """Minimal fake for requests.Response."""

    def __init__(self, text="", status_code=200):
        self.text = text
        self.status_code = status_code

    def json(self):
        import json

        return json.loads(self.text)


def _make_vehicle(engine_type=ENGINE_TYPES.EV):
    v = Vehicle(
        id="test-id",
        name="Ioniq 9",
        model="IONIQ 9",
        key="test-key",
        timezone=dt.timezone(dt.timedelta(hours=-5)),
    )
    v.engine_type = engine_type
    return v


def _make_token():
    return MagicMock(spec=Token)


def _make_api():
    """Create a HyundaiBlueLinkApiUSA without calling __init__."""
    api = object.__new__(HyundaiBlueLinkApiUSA)
    api.API_URL = "https://api.telematics.hyundaiusa.com/ac/v2/"
    api.sessions = MagicMock()
    return api


# ---------------------------------------------------------------------------
# start_charge tests
# ---------------------------------------------------------------------------


class TestStartCharge:
    def test_start_charge_normal_json_response(self):
        """start_charge succeeds when API returns valid JSON."""
        api = _make_api()
        api.sessions.post.return_value = _FakeResponse(
            text='{"status": "success"}', status_code=200
        )
        vehicle = _make_vehicle()
        token = _make_token()

        api.start_charge(token, vehicle)
        api.sessions.post.assert_called_once()

    def test_start_charge_empty_body_no_exception(self):
        """start_charge must NOT raise JSONDecodeError when API returns empty body.

        Regression test for: Expecting value: line 1 column 1 (char 0)
        Observed on 2026 Hyundai Ioniq 9 (USA) — command succeeds on vehicle
        but API returns HTTP 200 with no response body.
        """
        api = _make_api()
        api.sessions.post.return_value = _FakeResponse(text="", status_code=200)
        vehicle = _make_vehicle()
        token = _make_token()

        api.start_charge(token, vehicle)
        api.sessions.post.assert_called_once()

    def test_start_charge_empty_body_logs_debug(self, caplog):
        """start_charge logs a debug message when response body is empty."""
        api = _make_api()
        api.sessions.post.return_value = _FakeResponse(text="", status_code=200)
        vehicle = _make_vehicle()
        token = _make_token()

        with caplog.at_level(logging.DEBUG):
            api.start_charge(token, vehicle)

        assert (
            "empty body" in caplog.text.lower()
            or "treating as success" in caplog.text.lower()
        )

    def test_start_charge_skipped_for_non_ev(self):
        """start_charge is a no-op for non-EV vehicles."""
        api = _make_api()
        vehicle = _make_vehicle(engine_type=ENGINE_TYPES.ICE)
        token = _make_token()

        api.start_charge(token, vehicle)
        api.sessions.post.assert_not_called()

    def test_start_charge_calls_correct_url(self):
        """start_charge POSTs to the evc/charge/start endpoint."""
        api = _make_api()
        api.sessions.post.return_value = _FakeResponse(
            text='{"status": "success"}', status_code=200
        )
        vehicle = _make_vehicle()
        token = _make_token()

        with patch.object(api, "_get_vehicle_headers", return_value={}):
            api.start_charge(token, vehicle)

        call_url = api.sessions.post.call_args[0][0]
        assert "evc/charge/start" in call_url


# ---------------------------------------------------------------------------
# stop_charge tests
# ---------------------------------------------------------------------------


class TestStopCharge:
    def test_stop_charge_normal_json_response(self):
        """stop_charge succeeds when API returns valid JSON."""
        api = _make_api()
        api.sessions.post.return_value = _FakeResponse(
            text='{"status": "success"}', status_code=200
        )
        vehicle = _make_vehicle()
        token = _make_token()

        api.stop_charge(token, vehicle)
        api.sessions.post.assert_called_once()

    def test_stop_charge_empty_body_no_exception(self):
        """stop_charge must NOT raise JSONDecodeError when API returns empty body.

        Regression test for: Expecting value: line 1 column 1 (char 0)
        Observed on 2026 Hyundai Ioniq 9 (USA) — command succeeds on vehicle
        but API returns HTTP 200 with no response body.
        """
        api = _make_api()
        api.sessions.post.return_value = _FakeResponse(text="", status_code=200)
        vehicle = _make_vehicle()
        token = _make_token()

        api.stop_charge(token, vehicle)
        api.sessions.post.assert_called_once()

    def test_stop_charge_empty_body_logs_debug(self, caplog):
        """stop_charge logs a debug message when response body is empty."""
        api = _make_api()
        api.sessions.post.return_value = _FakeResponse(text="", status_code=200)
        vehicle = _make_vehicle()
        token = _make_token()

        with caplog.at_level(logging.DEBUG):
            api.stop_charge(token, vehicle)

        assert (
            "empty body" in caplog.text.lower()
            or "treating as success" in caplog.text.lower()
        )

    def test_stop_charge_skipped_for_non_ev(self):
        """stop_charge is a no-op for non-EV vehicles."""
        api = _make_api()
        vehicle = _make_vehicle(engine_type=ENGINE_TYPES.ICE)
        token = _make_token()

        api.stop_charge(token, vehicle)
        api.sessions.post.assert_not_called()

    def test_stop_charge_calls_correct_url(self):
        """stop_charge POSTs to the evc/charge/stop endpoint."""
        api = _make_api()
        api.sessions.post.return_value = _FakeResponse(
            text='{"status": "success"}', status_code=200
        )
        vehicle = _make_vehicle()
        token = _make_token()

        with patch.object(api, "_get_vehicle_headers", return_value={}):
            api.stop_charge(token, vehicle)

        call_url = api.sessions.post.call_args[0][0]
        assert "evc/charge/stop" in call_url
