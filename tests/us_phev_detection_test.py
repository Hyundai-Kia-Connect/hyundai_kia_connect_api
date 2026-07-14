"""Tests for PHEV detection in HyundaiBlueLinkApiUSA.get_vehicles.

Hyundai USA enrollment details return evStatus == "P" for plug-in hybrids.
get_vehicles must map this to ENGINE_TYPES.PHEV (previously left as None,
misclassifying PHEVs as unknown/ICE).
"""

import datetime as dt
import json
from unittest.mock import MagicMock, patch

from hyundai_kia_connect_api.HyundaiBlueLinkApiUSA import HyundaiBlueLinkApiUSA
from hyundai_kia_connect_api.const import ENGINE_TYPES


class _FakeResponse:
    def __init__(self, text=""):
        self.text = text

    def json(self):
        return json.loads(self.text)


def _make_api():
    api = object.__new__(HyundaiBlueLinkApiUSA)
    api.API_URL = "https://api.telematics.hyundaiusa.com/ac/v2/"
    api.session = MagicMock()
    api.data_timezone = dt.timezone.utc
    return api


def _enrollment_response(ev_status="P"):
    body = {
        "enrolledVehicleDetails": [
            {
                "vehicleDetails": {
                    "regid": "rid1",
                    "nickName": "Test Vehicle",
                    "vin": "VIN123",
                    "evStatus": ev_status,
                    "modelCode": "TMU",
                    "enrollmentDate": "2024-01-01",
                    "enrollmentStatus": "ACTIVE",
                    "vehicleGeneration": "3",
                }
            }
        ]
    }
    return _FakeResponse(text=json.dumps(body))


def _get_vehicles(api, ev_status):
    api.session.get = MagicMock(return_value=_enrollment_response(ev_status))
    with patch.object(api, "_get_authenticated_headers", return_value={}):
        return api.get_vehicles(MagicMock())


class TestPhevDetection:
    def test_phev_detected(self):
        api = _make_api()
        vehicles = _get_vehicles(api, "P")
        assert len(vehicles) == 1
        assert vehicles[0].engine_type == ENGINE_TYPES.PHEV

    def test_ev_still_detected(self):
        """Regression guard: EV must still map to ENGINE_TYPES.EV."""
        api = _make_api()
        vehicles = _get_vehicles(api, "E")
        assert vehicles[0].engine_type == ENGINE_TYPES.EV

    def test_ice_still_detected(self):
        """Regression guard: ICE must still map to ENGINE_TYPES.ICE."""
        api = _make_api()
        vehicles = _get_vehicles(api, "N")
        assert vehicles[0].engine_type == ENGINE_TYPES.ICE

    def test_unknown_ev_status_stays_none(self):
        """Regression guard: unknown evStatus leaves engine_type as None."""
        api = _make_api()
        vehicles = _get_vehicles(api, "X")
        assert vehicles[0].engine_type is None
