"""Tests for the CA maintenance-report gating: the list call runs every
update, the report call only when the newest id changes or is unknown."""

from unittest.mock import MagicMock

from hyundai_kia_connect_api.KiaUvoApiCA import KiaUvoApiCA
from hyundai_kia_connect_api.Token import Token
from hyundai_kia_connect_api.Vehicle import Vehicle


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload

    @property
    def text(self):
        import json

        return json.dumps(self._payload)


def _make_api() -> KiaUvoApiCA:
    api = KiaUvoApiCA(region=2, brand=1, language="en")
    api._sessions = MagicMock()
    return api


def _vehicle(**kwargs) -> Vehicle:
    v = Vehicle()
    v.id = "v1"
    return v


def _token() -> Token:
    return Token(username="u", password="p", access_token="AT", refresh_token="RT")


def test_update_skips_report_when_report_id_already_stored():
    api = _make_api()
    vehicle = _vehicle()
    vehicle.maintenance_report_last_id = 1002003001
    api._sessions.post.return_value = _FakeResponse(
        {
            "responseHeader": {"responseCode": 0},
            "result": {"MVRs": [{"reportId": 1002003001, "reportDate": "2026-10-25"}]},
        }
    )
    api._update_maintenance_report(_token(), vehicle)
    assert api._sessions.post.call_count == 1  # list only, no report fetch
    assert vehicle.maintenance_report_date is None


def test_update_skips_everything_on_empty_list():
    api = _make_api()
    vehicle = _vehicle()
    api._sessions.post.return_value = _FakeResponse(
        {"responseHeader": {"responseCode": 0}, "result": {"MVRs": []}}
    )
    api._update_maintenance_report(_token(), vehicle)
    assert api._sessions.post.call_count == 1
    assert vehicle.maintenance_report_last_id is None


def test_update_skips_everything_when_list_is_not_a_dict():
    api = _make_api()
    vehicle = _vehicle()
    api._sessions.post.return_value = _FakeResponse(
        {"responseHeader": {"responseCode": 0}, "result": "unexpected"}
    )
    api._update_maintenance_report(_token(), vehicle)
    assert api._sessions.post.call_count == 1


def test_update_skips_report_when_report_id_missing():
    api = _make_api()
    vehicle = _vehicle()
    api._sessions.post.return_value = _FakeResponse(
        {
            "responseHeader": {"responseCode": 0},
            "result": {"MVRs": [{"reportDate": "2026-10-25"}]},
        }
    )
    api._update_maintenance_report(_token(), vehicle)
    assert api._sessions.post.call_count == 1
    assert vehicle.maintenance_report_last_id is None


def test_unknown_report_id_on_existing_vehicle_refetches_report():
    api = _make_api()
    vehicle = _vehicle()
    vehicle.maintenance_report_last_id = 1002003001
    api._sessions.post.side_effect = [
        _FakeResponse(
            {
                "responseHeader": {"responseCode": 0},
                "result": {
                    "MVRs": [{"reportId": 1002003002, "reportDate": "2026-11-25"}]
                },
            }
        ),
        _FakeResponse(
            {
                "responseHeader": {"responseCode": 0},
                "result": {
                    "MVR": {
                        "reportDate": "2026-11-25",
                        "odometer": 78123.4,
                        "odometerUnitStr": "km",
                        "systems": [],
                    }
                },
            }
        ),
    ]
    api._update_maintenance_report(_token(), vehicle)
    assert api._sessions.post.call_count == 2
    assert vehicle.maintenance_report_last_id == 1002003002
    assert vehicle.maintenance_report_date == "2026-11-25"
    assert vehicle.maintenance_report_odometer == 78123.4
    assert vehicle.maintenance_report_odometer_unit == "km"
