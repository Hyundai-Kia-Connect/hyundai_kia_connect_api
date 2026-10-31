"""Tests for the Brazilian Hyundai BlueLink API.

Covers the asynchronous force-refresh flow (wake + sleep + read + staleness
guard). The cached-status endpoint (/ccs2/carstatus/latest, never
/status/latest) is covered by tests/test_vehicle_update_snapshots.py.

BR reports ``ccuCCS2ProtocolSupport: 0`` but serves status in CCS2 format at
``/ccs2/carstatus/latest``, so BR extends ``ApiImplType1`` and reuses its
``_update_vehicle_properties_ccs2`` parser.
"""

from unittest.mock import MagicMock, patch

import pytest

from hyundai_kia_connect_api.const import BRAND_HYUNDAI, BRANDS, REGION_BRAZIL, REGIONS
from hyundai_kia_connect_api.HyundaiBlueLinkApiBR import HyundaiBlueLinkApiBR
from hyundai_kia_connect_api.Vehicle import Vehicle
from tests.fixture_helpers import discover_fixtures, load_fixture

BR_FIXTURE_FILES = discover_fixtures("br_")

_BR_REGION = next(k for k, v in REGIONS.items() if v == REGION_BRAZIL)
_HYUNDAI_BRAND = next(k for k, v in BRANDS.items() if v == BRAND_HYUNDAI)


def _response(json_data):
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = json_data
    resp.raise_for_status.return_value = None
    return resp


def _fixture_state():
    """Return the CCS2 state.Vehicle dict from the first BR fixture."""
    data = load_fixture(BR_FIXTURE_FILES[0])
    return data["resMsg"]["state"]["Vehicle"]


@pytest.fixture
def br_api() -> HyundaiBlueLinkApiBR:
    return HyundaiBlueLinkApiBR(region=_BR_REGION, brand=_HYUNDAI_BRAND)


@pytest.fixture
def token() -> MagicMock:
    tok = MagicMock()
    tok.access_token = "test-access-token"
    tok.device_id = "test-device-id"
    return tok


def _called_urls(session_get):
    urls = []
    for call in session_get.call_args_list:
        urls.append(call.args[0] if call.args else call.kwargs["url"])
    return urls


class TestBRInheritance:
    def test_extends_apiimpltype1_and_reuses_ccs2_parser(self, br_api):
        # BR must inherit the CCS2 parser rather than duplicate it.
        assert hasattr(br_api, "_update_vehicle_properties_ccs2")
        assert type(br_api).__mro__[1].__name__ == "ApiImplType1"

    def test_valet_mode_disabled(self, br_api):
        # ApiImplType1 defaults valet mode on; BR must keep it off.
        assert br_api.supports_valet_mode is False


class TestBRCachedEndpoint:
    """The cached read must use /ccs2/carstatus/latest (never /status*)."""


class TestBRForceRefresh:
    """Force refresh: baseline /latest, wake /ccs2/carstatus, sleep(25),
    read /latest; apply only if lastUpdateTime advanced, else raise (no stale
    apply)."""

    def _state_payload(self, state, last_update):
        return {"resMsg": {"lastUpdateTime": last_update, "state": {"Vehicle": state}}}

    def test_force_wakes_sleeps_reads_and_applies_fresh(self, br_api, token):
        state = _fixture_state()
        ack = {"retCode": "S", "resCode": "0000"}
        baseline = self._state_payload(state, "T1")
        fresh = self._state_payload(state, "T2")

        latest_read = {"n": 0}

        def _get(url, headers=None):
            if url.endswith("/ccs2/carstatus") and not url.endswith("/latest"):
                return _response(ack)
            latest_read["n"] += 1
            return _response(baseline if latest_read["n"] == 1 else fresh)

        br_api.session = MagicMock()
        br_api.session.get.side_effect = _get
        vehicle = Vehicle(id="VID", ccu_ccs2_protocol_support=0)

        sleep_calls = []
        with patch(
            "hyundai_kia_connect_api.HyundaiBlueLinkApiBR.sleep",
            side_effect=lambda s: sleep_calls.append(s),
        ):
            br_api.force_refresh_vehicle_state(token, vehicle)

        urls = _called_urls(br_api.session.get)
        # baseline /latest, wake /ccs2/carstatus, post /latest
        assert len(urls) == 3
        assert urls[0].endswith("/ccs2/carstatus/latest")
        assert urls[1].endswith("/ccs2/carstatus")
        assert urls[2].endswith("/ccs2/carstatus/latest")
        assert sleep_calls == [25]
        assert vehicle.car_battery_percentage == 59

    def test_force_raises_when_snapshot_did_not_advance(self, br_api, token):
        """If lastUpdateTime is unchanged after the wake+sleep, the car did not
        report — do not apply stale data, raise APIError (audit-1: don't mask
        unknown as current)."""
        state = _fixture_state()
        same = self._state_payload(state, "T1")
        ack = {"retCode": "S", "resCode": "0000"}

        def _get(url, headers=None):
            if url.endswith("/ccs2/carstatus") and not url.endswith("/latest"):
                return _response(ack)
            return _response(same)  # baseline and post are identical

        br_api.session = MagicMock()
        br_api.session.get.side_effect = _get
        vehicle = Vehicle(id="VID", ccu_ccs2_protocol_support=0)

        with (
            patch("hyundai_kia_connect_api.HyundaiBlueLinkApiBR.sleep"),
            pytest.raises(Exception, match="did not return fresh data"),
        ):
            br_api.force_refresh_vehicle_state(token, vehicle)

        # Stale state was not applied.
        assert vehicle.car_battery_percentage is None
