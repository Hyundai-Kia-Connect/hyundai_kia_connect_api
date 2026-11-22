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
from hyundai_kia_connect_api.Token import Token
from hyundai_kia_connect_api.Vehicle import Vehicle
from tests.fixture_helpers import FIXTURES_DIR


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
    api.data_timezone = dt.UTC
    if response_text is not None:
        api.session.get = MagicMock(return_value=_FakeResponse(text=response_text))
    return api


def _make_vehicle(vid="rid1"):
    return Vehicle(
        id=vid,
        name="Test",
        model="TMU",
        key="key1",
        timezone=dt.UTC,
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


class TestEnrollmentCacheExpiry:
    """The enrollment response carries the odometer, so the cache must expire.

    Real captures from kia_uvo #1455 (2025 GV70 EV): the cached status has no
    odometer and there are no trip records, so enrollment/details is the only
    odometer source.
    """

    @staticmethod
    def _fixture(name):
        body = json.loads((FIXTURES_DIR / "updates" / "responses" / name).read_text())
        body.pop("_fixture_meta", None)
        return body

    def _api(self, enrollment):
        bodies = {
            "enrollment/details": lambda: enrollment,
            "rcs/rvs/vehicleStatus": lambda: self._fixture(
                "usa_genesis_vehicle_status_1455.json"
            ),
            "evTripDetails": lambda: self._fixture("us_hyundai_evtripdetails.json"),
            "rcs/rfc/findMyCar": lambda: self._fixture(
                "us_hyundai_findmycar_feature_off.json"
            ),
        }

        def get(url, headers=None):
            body = next(make() for path, make in bodies.items() if path in url)
            return _FakeResponse(text=json.dumps(body))

        api = _make_api()
        api.API_HEADERS = {}
        api.session.get = MagicMock(side_effect=get)
        return api

    def _enrollment_fetches(self, api):
        return sum(
            "enrollment/details" in call.args[0]
            for call in api.session.get.call_args_list
        )

    def test_odometer_follows_enrollment_after_the_cache_expires(self):
        enrollment = self._fixture("usa_genesis_enrollment_1455.json")
        details = enrollment["enrolledVehicleDetails"][0]["vehicleDetails"]
        api = self._api(enrollment)
        token = Token(username="user", password="pw", access_token="token")

        vehicle = api.get_vehicles(token)[0]
        api.update_vehicle_with_cached_state(token, vehicle)
        assert vehicle.odometer == 17332

        details["odometer"] = 17482
        api.update_vehicle_with_cached_state(token, vehicle)
        assert vehicle.odometer == 17332
        assert self._enrollment_fetches(api) == 1

        api._enrollment_details_cached_at -= api._ENROLLMENT_DETAILS_TTL_SECONDS
        api.update_vehicle_with_cached_state(token, vehicle)
        assert vehicle.odometer == 17482
        assert self._enrollment_fetches(api) == 2
