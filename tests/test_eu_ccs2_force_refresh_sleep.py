"""Unit test for the simpler wake+sleep+read CCS2 force-refresh (counter-PR to #1184)."""

import pytest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from hyundai_kia_connect_api.KiaUvoApiEU import KiaUvoApiEU
from hyundai_kia_connect_api.Vehicle import Vehicle


@pytest.fixture
def eu_api() -> KiaUvoApiEU:
    api = KiaUvoApiEU.__new__(KiaUvoApiEU)
    api.SPA_API_URL = "https://test.invalid/api/v1/spa/"
    api.session = MagicMock()
    api._get_authenticated_headers = MagicMock(
        return_value={"Authorization": "Bearer x"}
    )
    api._update_vehicle_properties_ccs2 = MagicMock()
    api._set_cached_location_park = MagicMock()
    return api


@pytest.fixture
def ccs2_vehicle() -> Vehicle:
    v = Vehicle()
    v.id = "vid-123"
    v.ccu_ccs2_protocol_support = 1
    return v


def test_force_refresh_wakes_sleeps_reads_latest_and_applies(eu_api, ccs2_vehicle):
    """wake /ccs2/carstatus (ack) -> sleep(25) -> read /latest -> apply + location."""
    token = SimpleNamespace(access_token="t", device_id="d")
    get_urls = []

    def mock_get(url, headers=None):
        get_urls.append(url)
        resp = MagicMock()
        if url.endswith("/ccs2/carstatus") and not url.endswith("/latest"):
            # wake endpoint: async ack envelope, NO resMsg
            resp.json.return_value = {"retCode": "S", "resCode": "0000", "msgId": "m1"}
        else:
            # /latest: cached snapshot with state.Vehicle
            resp.json.return_value = {
                "retCode": "S",
                "resCode": "0000",
                "resMsg": {"state": {"Vehicle": {"Date": "20260724120000.000"}}},
            }
        return resp

    sleep_calls = []

    with (
        patch.object(eu_api.session, "get", side_effect=mock_get),
        patch(
            "hyundai_kia_connect_api.KiaUvoApiEU.sleep",
            side_effect=lambda s: sleep_calls.append(s),
        ),
    ):
        eu_api._force_refresh_vehicle_state_ccs2(token, ccs2_vehicle)

    # wake + /latest read (2 GETs, distinct URLs)
    assert len(get_urls) == 2
    assert get_urls[0].endswith("/ccs2/carstatus")
    assert get_urls[1].endswith("/ccs2/carstatus/latest")
    # slept once, 25s
    assert sleep_calls == [25]
    # applied the /latest state + refreshed location
    eu_api._update_vehicle_properties_ccs2.assert_called_once()
    applied_state = eu_api._update_vehicle_properties_ccs2.call_args[0][1]
    assert applied_state == {"Date": "20260724120000.000"}
    eu_api._set_cached_location_park.assert_called_once_with(token, ccs2_vehicle)


def test_force_refresh_does_not_read_resMsg_from_wake_endpoint(eu_api, ccs2_vehicle):
    """The bug (KeyError: 'resMsg') must not recur: wake response has no resMsg,
    the method must read state from /latest, not from the wake response."""
    token = SimpleNamespace(access_token="t", device_id="d")

    def mock_get(url, headers=None):
        resp = MagicMock()
        if url.endswith("/ccs2/carstatus") and not url.endswith("/latest"):
            resp.json.return_value = {
                "retCode": "S",
                "resCode": "0000",
                "msgId": "m1",
            }  # no resMsg
        else:
            resp.json.return_value = {
                "retCode": "S",
                "resMsg": {"state": {"Vehicle": {"Date": "20260724120000.000"}}},
            }
        return resp

    with (
        patch.object(eu_api.session, "get", side_effect=mock_get),
        patch("hyundai_kia_connect_api.KiaUvoApiEU.sleep"),
    ):
        # must not raise KeyError: 'resMsg'
        eu_api._force_refresh_vehicle_state_ccs2(token, ccs2_vehicle)

    eu_api._update_vehicle_properties_ccs2.assert_called_once()
