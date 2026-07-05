"""Tests for findMyCar per-call timeout in HyundaiBlueLinkApiUSA.

_get_vehicle_location should pass a short per-call timeout so a hanging
findMyCar endpoint fails fast (returning None) instead of blocking the
coordinator update for up to 30s (session default read timeout).
"""

import datetime as dt
import json
from unittest.mock import MagicMock, patch

from hyundai_kia_connect_api.HyundaiBlueLinkApiUSA import HyundaiBlueLinkApiUSA
from hyundai_kia_connect_api.Vehicle import Vehicle


class _FakeResponse:
    def __init__(self, text=""):
        self.text = text

    def json(self):
        return json.loads(self.text)


def _make_api():
    api = object.__new__(HyundaiBlueLinkApiUSA)
    api.API_URL = "https://api.telematics.hyundaiusa.com/ac/v2/"
    api.session = MagicMock()
    return api


def _make_vehicle():
    return Vehicle(
        id="test-id",
        name="Tucson",
        model="Tucson",
        key="test-key",
        timezone=dt.timezone.utc,
    )


class TestFindMyCarTimeout:
    def test_findmycar_uses_short_timeout(self):
        """session.get for findMyCar must be called with timeout=(5, 10)."""
        api = _make_api()
        token = MagicMock()
        vehicle = _make_vehicle()
        api.session.get = MagicMock(
            return_value=_FakeResponse(text='{"coord": {"lat": 1.0, "lon": 2.0}}')
        )
        with patch.object(api, "_get_vehicle_headers", return_value={"X": "y"}):
            api._get_vehicle_location(token, vehicle)
        api.session.get.assert_called_once()
        _, kwargs = api.session.get.call_args
        assert kwargs.get("timeout") == (5, 10)

    def test_findmycar_timeout_failure_returns_none(self):
        """On timeout, _get_vehicle_location returns None (graceful)."""
        from requests.exceptions import Timeout

        api = _make_api()
        token = MagicMock()
        vehicle = _make_vehicle()
        api.session.get = MagicMock(side_effect=Timeout("read timeout"))
        with patch.object(api, "_get_vehicle_headers", return_value={"X": "y"}):
            result = api._get_vehicle_location(token, vehicle)
        assert result is None
