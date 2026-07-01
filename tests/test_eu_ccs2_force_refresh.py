"""Tests for CCS2 force_refresh_vehicle_state polling loop.

CCS2 force refresh: trigger /ccs2/carstatus, then poll /latest
until Date > trigger_time, max 10 attempts × 2s.
"""

import pytest
from unittest.mock import MagicMock, patch

from hyundai_kia_connect_api.KiaUvoApiEU import KiaUvoApiEU
from hyundai_kia_connect_api.Token import Token
from hyundai_kia_connect_api.Vehicle import Vehicle
from hyundai_kia_connect_api.const import DISTANCE_UNITS, ENGINE_TYPES


@pytest.fixture
def eu_api():
    return KiaUvoApiEU(region=1, brand=2, language="en")


@pytest.fixture
def ccs2_vehicle():
    v = Vehicle()
    v.id = "test-vehicle-id"
    v.engine_type = ENGINE_TYPES.PHEV
    v.ccu_ccs2_protocol_support = 1
    v.distance_unit = DISTANCE_UNITS[1]  # km
    return v


@pytest.fixture
def token():
    t = Token()
    t.access_token = "Bearer test-access-token"
    t.pin = "1234"
    t.device_id = "test-device-id"
    return t


def _make_ccs2_response(date_str, ret_code="S"):
    """Build a CCS2 /latest response with given Date."""
    return {
        "retCode": ret_code,
        "resCode": "0000",
        "resMsg": {
            "state": {
                "Vehicle": {
                    "Date": date_str,
                    "Cabin": {"HVAC": {}},
                    "Drivetrain": {},
                    "Chassis": {},
                    "Electronics": {},
                    "Green": {},
                    "Service": {},
                }
            }
        },
        "msgId": "test-msg-id",
    }


class TestCCS2ForceRefreshPolling:
    def test_trigger_then_poll_until_fresh(self, eu_api, token, ccs2_vehicle):
        """Should trigger /carstatus, then poll /latest until Date > trigger_time."""
        fresh_date = "20990616183000"  # far future → always > trigger_time
        stale_date = "20200101120000"  # past → always < trigger_time

        call_log = []

        def mock_get(url, **kwargs):
            resp = MagicMock()
            if url.endswith("/ccs2/carstatus") and not url.endswith("/latest"):
                call_log.append(("trigger", url))
                resp.json.return_value = {"retCode": "S", "resCode": "0000"}
            elif url.endswith("/latest"):
                call_log.append(("poll", url))
                if len([c for c in call_log if c[0] == "poll"]) <= 1:
                    resp.json.return_value = _make_ccs2_response(stale_date)
                else:
                    resp.json.return_value = _make_ccs2_response(fresh_date)
            else:
                resp.json.return_value = {
                    "retCode": "S",
                    "resCode": "0000",
                    "resMsg": {
                        "coord": {"lat": 50.0, "lon": 20.0, "alt": 0, "type": 0},
                        "head": 0,
                        "speed": {"value": 0, "unit": 0},
                        "time": "20260616183000",
                    },
                }
            return resp

        with (
            patch.object(eu_api.session, "get", side_effect=mock_get),
            patch("hyundai_kia_connect_api.KiaUvoApiEU.sleep"),
            patch.object(eu_api, "_update_vehicle_properties_ccs2"),
            patch.object(eu_api, "_set_cached_location_park"),
        ):
            eu_api._force_refresh_vehicle_state_ccs2(token, ccs2_vehicle)

        trigger_calls = [c for c in call_log if c[0] == "trigger"]
        assert len(trigger_calls) == 1
        assert "/ccs2/carstatus" in trigger_calls[0][1]

        poll_calls = [c for c in call_log if c[0] == "poll"]
        assert len(poll_calls) == 2  # stopped after fresh data

    def test_timeout_uses_last_fetched_data(self, eu_api, token, ccs2_vehicle):
        """On timeout (all polls stale), should still use last fetched data."""
        stale_date = "20200101120000"

        call_log = []

        def mock_get(url, **kwargs):
            resp = MagicMock()
            if url.endswith("/ccs2/carstatus") and not url.endswith("/latest"):
                call_log.append(("trigger", url))
                resp.json.return_value = {"retCode": "S", "resCode": "0000"}
            elif url.endswith("/latest"):
                call_log.append(("poll", url))
                resp.json.return_value = _make_ccs2_response(stale_date)
            else:
                resp.json.return_value = {
                    "retCode": "S",
                    "resCode": "0000",
                    "resMsg": {
                        "coord": {"lat": 50.0, "lon": 20.0, "alt": 0, "type": 0},
                        "head": 0,
                        "speed": {"value": 0, "unit": 0},
                        "time": "20260616183000",
                    },
                }
            return resp

        with (
            patch.object(eu_api.session, "get", side_effect=mock_get),
            patch("hyundai_kia_connect_api.KiaUvoApiEU.sleep"),
            patch.object(eu_api, "_update_vehicle_properties_ccs2"),
            patch.object(eu_api, "_set_cached_location_park"),
        ):
            eu_api._force_refresh_vehicle_state_ccs2(token, ccs2_vehicle)

        poll_calls = [c for c in call_log if c[0] == "poll"]
        assert len(poll_calls) == 10  # all attempts used

    def test_immediate_fresh_data(self, eu_api, token, ccs2_vehicle):
        """First poll returns fresh data — should stop immediately."""
        fresh_date = "20990616183000"

        call_log = []

        def mock_get(url, **kwargs):
            resp = MagicMock()
            if url.endswith("/ccs2/carstatus") and not url.endswith("/latest"):
                call_log.append(("trigger", url))
                resp.json.return_value = {"retCode": "S", "resCode": "0000"}
            elif url.endswith("/latest"):
                call_log.append(("poll", url))
                resp.json.return_value = _make_ccs2_response(fresh_date)
            else:
                resp.json.return_value = {
                    "retCode": "S",
                    "resCode": "0000",
                    "resMsg": {
                        "coord": {"lat": 50.0, "lon": 20.0, "alt": 0, "type": 0},
                        "head": 0,
                        "speed": {"value": 0, "unit": 0},
                        "time": "20260616183000",
                    },
                }
            return resp

        with (
            patch.object(eu_api.session, "get", side_effect=mock_get),
            patch("hyundai_kia_connect_api.KiaUvoApiEU.sleep"),
            patch.object(eu_api, "_update_vehicle_properties_ccs2"),
            patch.object(eu_api, "_set_cached_location_park"),
        ):
            eu_api._force_refresh_vehicle_state_ccs2(token, ccs2_vehicle)

        poll_calls = [c for c in call_log if c[0] == "poll"]
        assert len(poll_calls) == 1
