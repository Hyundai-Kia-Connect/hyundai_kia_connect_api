"""Tests for the Brazilian Hyundai BlueLink API.

Covers:
  * the cached-status endpoint (regression for the /status/latest 503 bug),
  * the asynchronous force-refresh flow, and
  * CCS2 status parsing driven by a JSON fixture.

BR reports ``ccuCCS2ProtocolSupport: 0`` but serves status in CCS2 format at
``/ccs2/carstatus/latest``, so BR extends ``ApiImplType1`` and reuses its
``_update_vehicle_properties_ccs2`` parser.
"""

from unittest.mock import MagicMock

import pytest

from hyundai_kia_connect_api.const import BRAND_HYUNDAI, BRANDS, REGION_BRAZIL, REGIONS
from hyundai_kia_connect_api.HyundaiBlueLinkApiBR import HyundaiBlueLinkApiBR
from hyundai_kia_connect_api.Vehicle import Vehicle

from tests.fixture_helpers import discover_fixtures, get_fixture_expected, load_fixture

BR_FIXTURE_FILES = discover_fixtures("br_")

_BR_REGION = [k for k, v in REGIONS.items() if v == REGION_BRAZIL][0]
_HYUNDAI_BRAND = [k for k, v in BRANDS.items() if v == BRAND_HYUNDAI][0]


def _response(json_data):
    resp = MagicMock()
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

    def test_cached_state_uses_ccs2_latest(self, br_api, token):
        state = _fixture_state()
        br_api.session = MagicMock()
        br_api.session.get.return_value = _response(
            {"resMsg": {"state": {"Vehicle": state}}}
        )
        vehicle = Vehicle(id="VID", ccu_ccs2_protocol_support=0)

        br_api.update_vehicle_with_cached_state(token, vehicle)

        urls = _called_urls(br_api.session.get)
        assert any(u.endswith("/spa/vehicles/VID/ccs2/carstatus/latest") for u in urls)
        assert not any("/status/latest" in u for u in urls)
        assert not any(u.endswith("/status") for u in urls)
        # Parsed via the inherited CCS2 parser.
        assert vehicle.car_battery_percentage == 59


class TestBRForceRefresh:
    """Force refresh triggers /ccs2/carstatus then polls /ccs2/carstatus/latest."""

    def test_force_triggers_and_polls_until_fresh(self, br_api, token):
        state = _fixture_state()
        old = {"resMsg": {"lastUpdateTime": "T1", "state": {"Vehicle": state}}}
        new = {"resMsg": {"lastUpdateTime": "T2", "state": {"Vehicle": state}}}
        ack = {"retCode": "S", "resCode": "0000"}
        br_api._FORCE_REFRESH_POLL_INTERVAL = 0

        latest_hits = {"n": 0}

        def side_effect(url, **kwargs):
            if url.endswith("/ccs2/carstatus"):  # async trigger
                return _response(ack)
            latest_hits["n"] += 1
            return _response(old if latest_hits["n"] == 1 else new)

        br_api.session = MagicMock()
        br_api.session.get.side_effect = side_effect
        vehicle = Vehicle(id="VID", ccu_ccs2_protocol_support=0)

        br_api.force_refresh_vehicle_state(token, vehicle)

        urls = _called_urls(br_api.session.get)
        assert any(u.endswith("/spa/vehicles/VID/ccs2/carstatus") for u in urls)
        assert vehicle.car_battery_percentage == 59

    def test_force_falls_back_to_cache_when_no_new_data(self, br_api, token):
        state = _fixture_state()
        same = {"resMsg": {"lastUpdateTime": "T1", "state": {"Vehicle": state}}}
        ack = {"retCode": "S", "resCode": "0000"}
        br_api._FORCE_REFRESH_POLL_INTERVAL = 0
        br_api._FORCE_REFRESH_MAX_POLLS = 2

        def side_effect(url, **kwargs):
            return (
                _response(ack) if url.endswith("/ccs2/carstatus") else _response(same)
            )

        br_api.session = MagicMock()
        br_api.session.get.side_effect = side_effect
        vehicle = Vehicle(id="VID", ccu_ccs2_protocol_support=0)

        # Should not raise, and still parse the (unchanged) cached snapshot.
        br_api.force_refresh_vehicle_state(token, vehicle)
        assert vehicle.car_battery_percentage == 59


@pytest.fixture
def properties_api() -> HyundaiBlueLinkApiBR:
    api = HyundaiBlueLinkApiBR.__new__(HyundaiBlueLinkApiBR)
    api.data_timezone = HyundaiBlueLinkApiBR.data_timezone
    return api


@pytest.fixture
def vehicle() -> Vehicle:
    return Vehicle()


@pytest.mark.parametrize("fixture_file", BR_FIXTURE_FILES, ids=BR_FIXTURE_FILES)
class TestBRUpdateVehicleProperties:
    def _parse(self, properties_api, vehicle, fixture_file):
        data = load_fixture(fixture_file)
        state = data["resMsg"]["state"]["Vehicle"]
        properties_api._update_vehicle_properties_ccs2(vehicle, state)
        return get_fixture_expected(data)

    def test_battery_and_engine(self, properties_api, vehicle, fixture_file):
        expected = self._parse(properties_api, vehicle, fixture_file)
        assert vehicle.car_battery_percentage == expected["car_battery_percentage"]
        assert bool(vehicle.engine_is_running) == expected["engine_is_running"]

    def test_is_locked(self, properties_api, vehicle, fixture_file):
        expected = self._parse(properties_api, vehicle, fixture_file)
        assert vehicle.is_locked == expected["is_locked"]

    def test_fuel_and_range(self, properties_api, vehicle, fixture_file):
        expected = self._parse(properties_api, vehicle, fixture_file)
        assert vehicle.fuel_level == expected["fuel_level"]
        assert vehicle.total_driving_range == expected["total_driving_range"]
        assert vehicle._total_driving_range_unit == expected["total_driving_range_unit"]

    def test_odometer(self, properties_api, vehicle, fixture_file):
        expected = self._parse(properties_api, vehicle, fixture_file)
        assert vehicle.odometer == expected["odometer"]
        assert vehicle._odometer_unit == expected["odometer_unit"]

    def test_last_updated_and_location_set(self, properties_api, vehicle, fixture_file):
        self._parse(properties_api, vehicle, fixture_file)
        # CCS2 'Date' populates last_updated_at (not now()), and location is parsed.
        assert vehicle.last_updated_at is not None
        assert vehicle.location is not None
