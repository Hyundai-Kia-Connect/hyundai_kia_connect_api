"""Tests for lock_action command in HyundaiBlueLinkApiUSA.

Covers the fix for JSONDecodeError when Hyundai's API returns an empty
response body (HTTP 200 OK, no body) for lock/unlock commands.
"""

import datetime as dt
import logging
from unittest.mock import MagicMock, patch

from hyundai_kia_connect_api.HyundaiBlueLinkApiUSA import HyundaiBlueLinkApiUSA
from hyundai_kia_connect_api.Token import Token
from hyundai_kia_connect_api.Vehicle import Vehicle
from hyundai_kia_connect_api.const import VEHICLE_LOCK_ACTION


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


class TestLockAction:
    def test_lock_empty_body_no_exception(self):
        """lock_action must NOT raise JSONDecodeError on empty body."""
        api = _make_api()
        api.sessions.post.return_value = _FakeResponse(text="", status_code=200)
        vehicle = _make_vehicle()
        token = _make_token()

        with patch.object(api, "_get_vehicle_headers", return_value={}):
            api.lock_action(token, vehicle, VEHICLE_LOCK_ACTION.LOCK)

        api.sessions.post.assert_called_once()

    def test_unlock_empty_body_no_exception(self):
        """lock_action with UNLOCK must NOT raise JSONDecodeError on empty body."""
        api = _make_api()
        api.sessions.post.return_value = _FakeResponse(text="", status_code=200)
        vehicle = _make_vehicle()
        token = _make_token()

        with patch.object(api, "_get_vehicle_headers", return_value={}):
            api.lock_action(token, vehicle, VEHICLE_LOCK_ACTION.UNLOCK)

        api.sessions.post.assert_called_once()

    def test_lock_normal_json_response(self):
        """lock_action succeeds when API returns valid JSON."""
        api = _make_api()
        api.sessions.post.return_value = _FakeResponse(
            text='{"status": "success"}', status_code=200
        )
        vehicle = _make_vehicle()
        token = _make_token()

        with patch.object(api, "_get_vehicle_headers", return_value={}):
            api.lock_action(token, vehicle, VEHICLE_LOCK_ACTION.LOCK)

        api.sessions.post.assert_called_once()

    def test_lock_empty_body_logs_debug(self, caplog):
        """lock_action logs debug message when response body is empty."""
        api = _make_api()
        api.sessions.post.return_value = _FakeResponse(text="", status_code=200)
        vehicle = _make_vehicle()
        token = _make_token()

        with caplog.at_level(logging.DEBUG):
            with patch.object(api, "_get_vehicle_headers", return_value={}):
                api.lock_action(token, vehicle, VEHICLE_LOCK_ACTION.LOCK)

        assert "empty" in caplog.text.lower()

    def test_lock_calls_correct_url(self):
        """lock_action POSTs to rcs/rdo/off for LOCK."""
        api = _make_api()
        api.sessions.post.return_value = _FakeResponse(
            text='{"status": "success"}', status_code=200
        )
        vehicle = _make_vehicle()
        token = _make_token()

        with patch.object(api, "_get_vehicle_headers", return_value={}):
            api.lock_action(token, vehicle, VEHICLE_LOCK_ACTION.LOCK)

        call_url = api.sessions.post.call_args[0][0]
        assert "rcs/rdo/off" in call_url

    def test_unlock_calls_correct_url(self):
        """lock_action POSTs to rcs/rdo/on for UNLOCK."""
        api = _make_api()
        api.sessions.post.return_value = _FakeResponse(
            text='{"status": "success"}', status_code=200
        )
        vehicle = _make_vehicle()
        token = _make_token()

        with patch.object(api, "_get_vehicle_headers", return_value={}):
            api.lock_action(token, vehicle, VEHICLE_LOCK_ACTION.UNLOCK)

        call_url = api.sessions.post.call_args[0][0]
        assert "rcs/rdo/on" in call_url
