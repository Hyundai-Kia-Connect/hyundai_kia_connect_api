"""Tests for the EV5-appMode flat scheduled-charging dispatch (#1764).

ccNC/EV5-appMode EVs (EV6/EV9) use two flat endpoints
(/ccs2/reservation/charge + /ccs2/reservation/hvac) instead of the combined
/ccs2/reservation/chargehvac. Dispatch is by ccu_ccs2_protocol_support (ccNC)
and engine_type == EV.
"""

import datetime as dt
from unittest.mock import MagicMock

import pytest

from hyundai_kia_connect_api.ApiImpl import ScheduleChargingClimateRequestOptions
from hyundai_kia_connect_api.ApiImplType1 import ApiImplType1
from hyundai_kia_connect_api.Token import Token
from hyundai_kia_connect_api.Vehicle import Vehicle
from hyundai_kia_connect_api.const import ENGINE_TYPES


@pytest.fixture
def api() -> ApiImplType1:
    api = ApiImplType1.__new__(ApiImplType1)
    api.data_timezone = None
    api.temperature_range = [x * 0.5 for x in range(28, 60)]
    api.SPA_API_URL_V2 = "https://example/api/v2/spa/"
    # session.post is mocked per-test; _get_control_headers returns a stub.
    api._get_control_headers = MagicMock(return_value={"Authorization": "ctrl"})
    return api


def _make_vehicle(ccs2: int, engine_type: ENGINE_TYPES) -> Vehicle:
    v = Vehicle()
    v.id = "vid-123"
    v.ccu_ccs2_protocol_support = ccs2
    v.engine_type = engine_type
    return v


def _mock_post(
    api: ApiImplType1, charge_id: str = "charge-id", hvac_id: str = "hvac-id"
) -> list:
    """Mock session.post; route by URL suffix. Returns the recorded calls."""
    calls: list = []

    def _post(url, json=None, headers=None, **kwargs):
        calls.append({"url": url, "json": json, "headers": headers})
        resp = MagicMock()
        if url.endswith("/charge"):
            resp.json.return_value = {
                "retCode": "S",
                "resCode": "0000",
                "msgId": charge_id,
            }
        elif url.endswith("/hvac"):
            resp.json.return_value = {
                "retCode": "S",
                "resCode": "0000",
                "msgId": hvac_id,
            }
        else:  # /chargehvac or any combined path
            resp.json.return_value = {
                "retCode": "S",
                "resCode": "0000",
                "msgId": "combined-id",
            }
        return resp

    api.session = MagicMock()
    api.session.post.side_effect = _post
    return calls


def _options(**kw) -> ScheduleChargingClimateRequestOptions:
    opts = ScheduleChargingClimateRequestOptions(
        charging_enabled=kw.get("charging_enabled", True),
        off_peak_start_time=kw.get("off_peak_start_time", dt.time(23, 30)),
        off_peak_end_time=kw.get("off_peak_end_time", dt.time(1, 30)),
        off_peak_charge_only_enabled=kw.get("off_peak_charge_only_enabled", True),
        climate_enabled=kw.get("climate_enabled", False),
        temperature=kw.get("temperature", 21.0),
        temperature_unit=kw.get("temperature_unit", 0),
        defrost=kw.get("defrost", False),
        first_departure=ScheduleChargingClimateRequestOptions.DepartureOptions(
            enabled=kw.get("first_departure_enabled", False),
            days=kw.get("first_departure_days", [0]),
            time=kw.get("first_departure_time", dt.time(7, 10)),
        ),
        second_departure=ScheduleChargingClimateRequestOptions.DepartureOptions(
            enabled=False, days=[0], time=dt.time()
        ),
    )
    return opts


class TestEV5Dispatch:
    def test_ccs2_ev_uses_flat_endpoints(self, api):
        calls = _mock_post(api)
        v = _make_vehicle(ccs2=1, engine_type=ENGINE_TYPES.EV)
        api.schedule_charging_and_climate(MagicMock(spec=Token), v, _options())
        urls = [c["url"] for c in calls]
        assert any(u.endswith("/ccs2/reservation/charge") for u in urls)
        assert any(u.endswith("/ccs2/reservation/hvac") for u in urls)
        assert not any(u.endswith("/reservation/chargehvac") for u in urls)

    def test_ccsp_ev_uses_combined(self, api):
        calls = _mock_post(api)
        v = _make_vehicle(ccs2=0, engine_type=ENGINE_TYPES.EV)
        api.schedule_charging_and_climate(MagicMock(spec=Token), v, _options())
        urls = [c["url"] for c in calls]
        assert any(u.endswith("/reservation/chargehvac") for u in urls)
        assert not any(u.endswith("/reservation/charge") for u in urls)
        assert not any(u.endswith("/reservation/hvac") for u in urls)

    def test_ccs2_phev_uses_combined(self, api):
        calls = _mock_post(api)
        v = _make_vehicle(ccs2=1, engine_type=ENGINE_TYPES.PHEV)
        api.schedule_charging_and_climate(MagicMock(spec=Token), v, _options())
        urls = [c["url"] for c in calls]
        assert any(u.endswith("/reservation/chargehvac") for u in urls)
        assert not any(u.endswith("/ccs2/reservation/charge") for u in urls)

    def test_ccs2_hev_uses_combined(self, api):
        calls = _mock_post(api)
        v = _make_vehicle(ccs2=1, engine_type=ENGINE_TYPES.HEV)
        api.schedule_charging_and_climate(MagicMock(spec=Token), v, _options())
        urls = [c["url"] for c in calls]
        assert any(u.endswith("/reservation/chargehvac") for u in urls)


class TestEV5FlatChargePayload:
    def test_charge_payload_keys_and_lowercase_flag(self, api):
        calls = _mock_post(api)
        v = _make_vehicle(ccs2=1, engine_type=ENGINE_TYPES.EV)
        api.schedule_charging_and_climate(MagicMock(spec=Token), v, _options())
        charge = next(c["json"] for c in calls if c["url"].endswith("/charge"))
        assert set(charge.keys()) == {
            "reservFlag",
            "offpeakPowerFlag",  # lowercase "offpeak", flat (not offPeakPowerInfo)
            "reservStartTime",
            "reservEndTime",
        }

    def test_offpeak_power_flag_time_vs_target(self, api):
        # off_peak_charge_only_enabled=True -> flag 1 (time-priority)
        calls = _mock_post(api)
        v = _make_vehicle(ccs2=1, engine_type=ENGINE_TYPES.EV)
        api.schedule_charging_and_climate(
            MagicMock(spec=Token), v, _options(off_peak_charge_only_enabled=True)
        )
        charge = next(c["json"] for c in calls if c["url"].endswith("/charge"))
        assert charge["offpeakPowerFlag"] == 1

    def test_offpeak_power_flag_target(self, api):
        calls = _mock_post(api)
        v = _make_vehicle(ccs2=1, engine_type=ENGINE_TYPES.EV)
        api.schedule_charging_and_climate(
            MagicMock(spec=Token), v, _options(off_peak_charge_only_enabled=False)
        )
        charge = next(c["json"] for c in calls if c["url"].endswith("/charge"))
        assert charge["offpeakPowerFlag"] == 2

    def test_reserv_flag_on_off(self, api):
        calls = _mock_post(api)
        v = _make_vehicle(ccs2=1, engine_type=ENGINE_TYPES.EV)
        api.schedule_charging_and_climate(
            MagicMock(spec=Token), v, _options(charging_enabled=False)
        )
        charge = next(c["json"] for c in calls if c["url"].endswith("/charge"))
        assert charge["reservFlag"] == 0

    def test_window_times_and_timesection(self, api):
        calls = _mock_post(api)
        v = _make_vehicle(ccs2=1, engine_type=ENGINE_TYPES.EV)
        api.schedule_charging_and_climate(
            MagicMock(spec=Token),
            v,
            _options(
                off_peak_start_time=dt.time(22, 30),  # PM -> "1030", section 1
                off_peak_end_time=dt.time(1, 30),  # AM -> "0130", section 0
            ),
        )
        charge = next(c["json"] for c in calls if c["url"].endswith("/charge"))
        assert charge["reservStartTime"] == {"time": "1030", "timeSection": 1}
        assert charge["reservEndTime"] == {"time": "0130", "timeSection": 0}


class TestEV5FlatHvacPayload:
    def test_hvac_payload_structure_no_heating1(self, api):
        calls = _mock_post(api)
        v = _make_vehicle(ccs2=1, engine_type=ENGINE_TYPES.EV)
        api.schedule_charging_and_climate(
            MagicMock(spec=Token),
            v,
            _options(
                climate_enabled=True,
                temperature=22.0,
                defrost=True,
                first_departure_enabled=True,
                first_departure_days=[1, 2, 3],
                first_departure_time=dt.time(7, 10),
            ),
        )
        hvac = next(c["json"] for c in calls if c["url"].endswith("/hvac"))
        assert set(hvac.keys()) == {"reservedHVACInfo1", "reservedHVACInfo2"}
        info1 = hvac["reservedHVACInfo1"]
        assert set(info1.keys()) == {"reservHVACflag", "reservInfo", "reservHVACSet"}
        assert info1["reservHVACflag"] == 1  # first departure enabled
        assert info1["reservInfo"]["day"] == [1, 2, 3]
        assert info1["reservInfo"]["time"] == {"time": "0710", "timeSection": 0}
        hvac_set = info1["reservHVACSet"]
        assert set(hvac_set.keys()) == {"airCtrl", "airTemp", "defrost"}  # no heating1
        assert hvac_set["airCtrl"] == 1  # climate_enabled
        assert hvac_set["defrost"] is True
        assert hvac_set["airTemp"]["value"] == "22.0"

    def test_hvac_reservhvacflag_disabled(self, api):
        calls = _mock_post(api)
        v = _make_vehicle(ccs2=1, engine_type=ENGINE_TYPES.EV)
        api.schedule_charging_and_climate(
            MagicMock(spec=Token), v, _options(first_departure_enabled=False)
        )
        hvac = next(c["json"] for c in calls if c["url"].endswith("/hvac"))
        assert hvac["reservedHVACInfo1"]["reservHVACflag"] == 0


class TestEV5Return:
    def test_returns_charge_msgid(self, api):
        _mock_post(api, charge_id="charge-xyz")
        v = _make_vehicle(ccs2=1, engine_type=ENGINE_TYPES.EV)
        msg_id = api.schedule_charging_and_climate(MagicMock(spec=Token), v, _options())
        assert msg_id == "charge-xyz"
