"""Tests for enrollment/details caching in HyundaiBlueLinkApiUSA.

_get_vehicle_details is called every refresh cycle by
update_vehicle_with_cached_state, but enrollment data (capabilities,
seat configs, generation) rarely changes. Cache the response on the
instance and reuse it; force-refresh only from get_vehicles (where the
vehicle list can change).
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


def _enrollment_body(regid="rid1", nick="Test"):
    return {
        "enrolledVehicleDetails": [
            {
                "vehicleDetails": {
                    "regid": regid,
                    "nickName": nick,
                    "vin": "VIN123",
                    "evStatus": "E",
                    "modelCode": "TMU",
                    "enrollmentDate": "2024-01-01",
                    "enrollmentStatus": "ACTIVE",
                    "vehicleGeneration": "3",
                }
            }
        ]
    }


def _make_api(response_text=None):
    api = object.__new__(HyundaiBlueLinkApiUSA)
    api.API_URL = "https://api.telematics.hyundaiusa.com/ac/v2/"
    api.session = MagicMock()
    api.data_timezone = dt.timezone.utc
    if response_text is not None:
        api.session.get = MagicMock(return_value=_FakeResponse(text=response_text))
    return api


def _make_vehicle(vid="rid1"):
    return Vehicle(
        id=vid,
        name="Test",
        model="TMU",
        key="key1",
        timezone=dt.timezone.utc,
    )


class TestEnrollmentCache:
    def test_initial_cache_is_none(self):
        api = _make_api()
        assert api._enrollment_details_cache is None

    def test_get_vehicle_details_caches_response(self):
        """Second _get_vehicle_details call must not hit session.get again."""
        body = json.dumps(_enrollment_body())
        api = _make_api(response_text=body)
        token = MagicMock()
        vehicle = _make_vehicle()
        with patch.object(api, "_get_authenticated_headers", return_value={}):
            api._get_vehicle_details(token, vehicle)
            api._get_vehicle_details(token, vehicle)
        assert api.session.get.call_count == 1

    def test_get_vehicles_force_refreshes_cache(self):
        """get_vehicles must hit session.get even when cache is populated."""
        body = json.dumps(_enrollment_body())
        api = _make_api(response_text=body)
        token = MagicMock()
        with patch.object(api, "_get_authenticated_headers", return_value={}):
            api._get_vehicle_details(token, _make_vehicle())
            assert api._enrollment_details_cache is not None
            api.get_vehicles(token)
        assert api.session.get.call_count == 2

    def test_get_vehicles_populates_cache_used_by_get_vehicle_details(self):
        """After get_vehicles refreshes cache, _get_vehicle_details reuses it."""
        body = json.dumps(_enrollment_body(regid="rid1", nick="Latest"))
        api = _make_api(response_text=body)
        token = MagicMock()
        with patch.object(api, "_get_authenticated_headers", return_value={}):
            api.get_vehicles(token)
            assert api._enrollment_details_cache is not None
            details = api._get_vehicle_details(token, _make_vehicle())
        # _get_vehicle_details used the cache populated by get_vehicles
        assert details["nickName"] == "Latest"
        # Only one session.get call (from get_vehicles); _get_vehicle_details
        # was a cache hit
        assert api.session.get.call_count == 1
