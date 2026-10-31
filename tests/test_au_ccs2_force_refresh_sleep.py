"""Unit test for the simpler wake+sleep+read CCS2 force-refresh in AU."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from hyundai_kia_connect_api.KiaUvoApiAU import KiaUvoApiAU
from hyundai_kia_connect_api.Vehicle import Vehicle


@pytest.fixture
def au_api() -> KiaUvoApiAU:
    api = KiaUvoApiAU.__new__(KiaUvoApiAU)
    api.SPA_API_URL = "https://test.invalid/api/v1/spa/"
    api.session = MagicMock()
    api._get_authenticated_headers = MagicMock(
        return_value={"Authorization": "Bearer x"}
    )
    api._update_vehicle_properties_ccs2 = MagicMock()
    api._get_location = MagicMock(return_value=None)
    return api


@pytest.fixture
def ccs2_vehicle() -> Vehicle:
    v = Vehicle()
    v.id = "vid-123"
    v.ccu_ccs2_protocol_support = 1
    return v


def test_force_refresh_wakes_sleeps_reads_latest_and_applies(au_api, ccs2_vehicle):
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
        patch.object(au_api.session, "get", side_effect=mock_get),
        patch(
            "hyundai_kia_connect_api.KiaUvoApiAU.sleep",
            side_effect=lambda s: sleep_calls.append(s),
        ),
    ):
        au_api._force_refresh_vehicle_state_ccs2(token, ccs2_vehicle)

    # wake + /latest read (2 GETs, distinct URLs)
    assert len(get_urls) == 2
    assert get_urls[0].endswith("/ccs2/carstatus")
    assert get_urls[1].endswith("/ccs2/carstatus/latest")
    # slept once, 25s
    assert sleep_calls == [25]
    # applied the /latest state + refreshed location
    au_api._update_vehicle_properties_ccs2.assert_called_once()
    applied_state = au_api._update_vehicle_properties_ccs2.call_args[0][1]
    assert applied_state == {"Date": "20260724120000.000"}
    au_api._get_location.assert_called_once_with(token, ccs2_vehicle)


def test_force_refresh_does_not_read_resMsg_from_wake_endpoint(au_api, ccs2_vehicle):
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
        patch.object(au_api.session, "get", side_effect=mock_get),
        patch("hyundai_kia_connect_api.KiaUvoApiAU.sleep"),
    ):
        # must not raise KeyError: 'resMsg'
        au_api._force_refresh_vehicle_state_ccs2(token, ccs2_vehicle)

    au_api._update_vehicle_properties_ccs2.assert_called_once()


def test_force_refresh_wake_failure_propagates_no_stale_apply(au_api, ccs2_vehicle):
    """A failed wake GET (non-JSON / network error) must propagate and NOT
    fall through to a stale /latest apply.
    """
    token = SimpleNamespace(access_token="t", device_id="d")

    def mock_get(url, headers=None):
        resp = MagicMock()
        if url.endswith("/ccs2/carstatus") and not url.endswith("/latest"):
            resp.json.side_effect = ValueError("not JSON")  # wake fails
        else:
            resp.json.return_value = {
                "retCode": "S",
                "resMsg": {"state": {"Vehicle": {"Date": "20260724120000.000"}}},
            }
        return resp

    with (
        patch.object(au_api.session, "get", side_effect=mock_get),
        patch("hyundai_kia_connect_api.KiaUvoApiAU.sleep"),
        pytest.raises(ValueError),
    ):
        au_api._force_refresh_vehicle_state_ccs2(token, ccs2_vehicle)

    # wake failed -> no sleep-then-/latest apply happened
    au_api._update_vehicle_properties_ccs2.assert_not_called()
    au_api._get_location.assert_not_called()
