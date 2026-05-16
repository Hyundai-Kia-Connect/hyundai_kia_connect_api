"""Tests for start_climate and stop_climate commands in HyundaiBlueLinkApiUSA.

Covers the fix for JSONDecodeError when Hyundai's API returns an empty
response body (HTTP 200 OK, no body) for climate commands.
"""

import datetime as dt
import logging
from unittest.mock import MagicMock, patch

from hyundai_kia_connect_api.HyundaiBlueLinkApiUSA import HyundaiBlueLinkApiUSA
from hyundai_kia_connect_api.Token import Token
from hyundai_kia_connect_api.Vehicle import Vehicle
from hyundai_kia_connect_api.ApiImpl import ClimateRequestOptions


class _FakeResponse:
    """Minimal fake for requests.Response."""

    def __init__(self, text="", status_code=200):
        self.text = text
        self.status_code = status_code

    def json(self):
        import json

        return json.loads(self.text)


def _make_vehicle():
    return Vehicle(
        id="test-id",
        name="Ioniq 5",
        model="IONIQ 5",
        key="test-key",
        timezone=dt.timezone(dt.timedelta(hours=-5)),
    )


def _make_token():
    return MagicMock(spec=Token)


def _make_api():
    """Create a HyundaiBlueLinkApiUSA without calling __init__."""
    api = object.__new__(HyundaiBlueLinkApiUSA)
    api.API_URL = "https://api.telematics.hyundaiusa.com/ac/v2/"
    api.sessions = MagicMock()
    return api


def _make_climate_options():
    return ClimateRequestOptions(set_temp=72, defrost=False, climate=True, heating=True)


class TestStartClimate:
    def test_start_climate_empty_body_no_exception(self):
        """start_climate must NOT raise JSONDecodeError on empty body."""
        api = _make_api()
        api.sessions.post.return_value = _FakeResponse(text="", status_code=200)
        vehicle = _make_vehicle()
        token = _make_token()
        options = _make_climate_options()

        with patch.object(api, "_get_vehicle_headers", return_value={}):
            api.start_climate(token, vehicle, options)

        api.sessions.post.assert_called_once()

    def test_start_climate_normal_json_response(self):
        """start_climate succeeds when API returns valid JSON."""
        api = _make_api()
        api.sessions.post.return_value = _FakeResponse(
            text='{"status": "success"}', status_code=200
        )
        vehicle = _make_vehicle()
        token = _make_token()
        options = _make_climate_options()

        with patch.object(api, "_get_vehicle_headers", return_value={}):
            api.start_climate(token, vehicle, options)

        api.sessions.post.assert_called_once()

    def test_start_climate_empty_body_logs_debug(self, caplog):
        """start_climate logs debug message when response body is empty."""
        api = _make_api()
        api.sessions.post.return_value = _FakeResponse(text="", status_code=200)
        vehicle = _make_vehicle()
        token = _make_token()
        options = _make_climate_options()

        with caplog.at_level(logging.DEBUG):
            with patch.object(api, "_get_vehicle_headers", return_value={}):
                api.start_climate(token, vehicle, options)

        assert "empty" in caplog.text.lower()


class TestStopClimate:
    def test_stop_climate_empty_body_no_exception(self):
        """stop_climate must NOT raise JSONDecodeError on empty body."""
        api = _make_api()
        api.sessions.post.return_value = _FakeResponse(text="", status_code=200)
        vehicle = _make_vehicle()
        token = _make_token()

        with patch.object(api, "_get_vehicle_headers", return_value={}):
            api.stop_climate(token, vehicle)

        api.sessions.post.assert_called_once()

    def test_stop_climate_normal_json_response(self):
        """stop_climate succeeds when API returns valid JSON."""
        api = _make_api()
        api.sessions.post.return_value = _FakeResponse(
            text='{"status": "success"}', status_code=200
        )
        vehicle = _make_vehicle()
        token = _make_token()

        with patch.object(api, "_get_vehicle_headers", return_value={}):
            api.stop_climate(token, vehicle)

        api.sessions.post.assert_called_once()

    def test_stop_climate_empty_body_logs_debug(self, caplog):
        """stop_climate logs debug message when response body is empty."""
        api = _make_api()
        api.sessions.post.return_value = _FakeResponse(text="", status_code=200)
        vehicle = _make_vehicle()
        token = _make_token()

        with caplog.at_level(logging.DEBUG):
            with patch.object(api, "_get_vehicle_headers", return_value={}):
                api.stop_climate(token, vehicle)

        assert "empty" in caplog.text.lower()
