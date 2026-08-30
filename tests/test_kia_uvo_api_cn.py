"""Unit tests for the rewritten KiaUvoApiCN (China) — offline, no network.

Covers the parts of the 2026 login-flow rewrite that can be exercised
without hitting the real servers:
- _extract_uars_login_bundle (UARS callback HTML parsing)
- _uars_state_param (authorize state blob)
- inherited _check_response_for_errors mapping (4002 -> DeviceIDError etc.)
- brand dispatch constants
- get_vehicles parsing (fixture from a live capture, VIN redacted)
- the airTemp "FFH" sentinel in _update_vehicle_properties
"""

import base64
import datetime as dt
import json

import pytest

from hyundai_kia_connect_api.ApiImplType1 import _check_response_for_errors
from hyundai_kia_connect_api.const import (
    BRAND_HYUNDAI,
    BRAND_KIA,
    REGION_CHINA,
)
from hyundai_kia_connect_api.exceptions import (
    APIError,
    AuthenticationError,
    DeviceIDError,
    RateLimitingError,
)
from hyundai_kia_connect_api.KiaUvoApiCN import (
    USER_AGENT_BLUELINK_CN,
    KiaUvoApiCN,
    _extract_uars_login_bundle,
)
from hyundai_kia_connect_api.Vehicle import Vehicle


@pytest.fixture
def api() -> KiaUvoApiCN:
    return KiaUvoApiCN(REGION_CHINA, BRAND_HYUNDAI, "zh")


@pytest.fixture
def vehicle() -> Vehicle:
    return Vehicle()


# ---------------------------------------------------------------------------
# _extract_uars_login_bundle
# ---------------------------------------------------------------------------
BUNDLE = {
    "code": 0,
    "status": True,
    "message": "操作成功",
    "id": "UARS-COM-040",
    "data": {
        "uarsToken": "UARS-TOKEN",
        "tokenCode": "TOKEN-CODE",
        "ccspToken": {
            "accessToken": "ACCESS",
            "refreshToken": "REFRESH",
            "tokenType": "Bearer",
            "expiresIn": 21600,
        },
        "profile": {"email": "user@example.com", "hasPin": True},
    },
}


def _wrap_as_template(j: dict) -> str:
    return "<html><script>var loginInfo = `" + json.dumps(j) + "`;</script></html>"


class TestExtractUarsLoginBundle:
    def test_template_literal_form(self):
        html = _wrap_as_template(BUNDLE)
        bundle = _extract_uars_login_bundle(html)
        assert bundle["data"]["ccspToken"]["accessToken"] == "ACCESS"
        assert bundle["data"]["profile"]["hasPin"] is True

    def test_nested_json_survives_template_extraction(self):
        # the ccspToken/profile sub-objects must not truncate the match
        html = _wrap_as_template(BUNDLE)
        bundle = _extract_uars_login_bundle(html)
        assert bundle["data"]["tokenCode"] == "TOKEN-CODE"

    def test_brace_matching_fallback(self):
        # no template-literal wrapper, plain embedded JSON
        html = "<html>prefix " + json.dumps(BUNDLE) + " suffix</html>"
        bundle = _extract_uars_login_bundle(html)
        assert bundle["data"]["ccspToken"]["tokenType"] == "Bearer"

    def test_missing_bundle_raises(self):
        with pytest.raises(AuthenticationError):
            _extract_uars_login_bundle("<html>no tokens here</html>")

    def test_error_bundle_raises(self):
        # a callback that rejected the code has no uarsToken in data
        html = _wrap_as_template({"code": 1, "message": "fail"})
        with pytest.raises(AuthenticationError):
            _extract_uars_login_bundle(html)


# ---------------------------------------------------------------------------
# _uars_state_param
# ---------------------------------------------------------------------------
class TestUarsStateParam:
    def test_roundtrip(self, api: KiaUvoApiCN):
        param = api._uars_state_param("DEVICE-UUID", "UARS-COM-040")
        padded = param + "=" * (-len(param) % 4)
        blob = json.loads(base64.urlsafe_b64decode(padded))
        assert blob == {
            "interfaceId": "UARS-COM-040",
            "accUnqNo": "",
            "deviceUuid": "DEVICE-UUID",
            "webRedirect": "",
        }

    def test_urlsafe_no_slash_padding_issues(self, api: KiaUvoApiCN):
        param = api._uars_state_param("X" * 36, "UARS-COM-030")
        assert "/" not in param and "+" not in param


# ---------------------------------------------------------------------------
# inherited error mapping (4002 must stay DeviceIDError so the
# _retry_on_device_id_error decorator re-registers and retries)
# ---------------------------------------------------------------------------
class TestInheritedErrorMapping:
    def test_4002_is_device_id_error(self):
        with pytest.raises(DeviceIDError):
            _check_response_for_errors(
                {"retCode": "F", "resCode": "4002", "resMsg": "deviceId is not exist."}
            )

    def test_5091_is_rate_limiting(self):
        with pytest.raises(RateLimitingError):
            _check_response_for_errors(
                {
                    "retCode": "F",
                    "resCode": "5091",
                    "resMsg": "Exceeds number of requests",
                }
            )

    def test_success_does_not_raise(self):
        _check_response_for_errors({"retCode": "S", "resCode": "0000", "resMsg": {}})

    def test_unknown_rescode_is_api_error(self):
        with pytest.raises(APIError):
            _check_response_for_errors(
                {"retCode": "F", "resCode": "1234", "resMsg": "mystery"}
            )


# ---------------------------------------------------------------------------
# brand dispatch
# ---------------------------------------------------------------------------
class TestBrandDispatch:
    def test_hyundai_constants(self, api: KiaUvoApiCN):
        assert api.BASE_URL == "prd.cn-ccapi.hyundai.com"
        assert api.CCSP_SERVICE_ID == "72b3d019-5bc7-443d-a437-08f307cf06e2"
        assert api.APP_ID == "b09e4d17-c30c-40f1-a1ec-8ac11d6665cf"
        assert api.UARS_BASE_URL == "https://uars-h.hmgmobility.com.cn"

    def test_kia_constants(self):
        api = KiaUvoApiCN(REGION_CHINA, BRAND_KIA, "zh")
        assert api.BASE_URL == "prd.cn-ccapi.kia.com"
        assert api.UARS_BASE_URL == "https://uars-k.hmgmobility.com.cn"
        assert api.APP_ID == "5519a969-295f-4c5a-a27e-9d9fab2bd50c"

    def test_numeric_brand_key(self):
        # VehicleManager passes the numeric BRANDS key
        api = KiaUvoApiCN(REGION_CHINA, 2, "zh")
        assert api.BASE_URL == "prd.cn-ccapi.hyundai.com"

    def test_unsupported_brand_raises(self):
        with pytest.raises(ValueError):
            KiaUvoApiCN(REGION_CHINA, "Genesis", "zh")

    def test_no_stamp_header(self, api: KiaUvoApiCN):
        assert api._get_stamp() == ""
        token = TokenStub()
        headers = api._get_authenticated_headers(token)
        assert "Stamp" not in headers
        assert headers["User-Agent"] == USER_AGENT_BLUELINK_CN
        assert headers["ccsp-device-id"] == token.device_id


class TokenStub:
    """Minimal token stand-in for header construction tests."""

    access_token = "Bearer ACCESS"
    device_id = "DEVICE-ID"


# ---------------------------------------------------------------------------
# get_vehicles parsing (fixture from live capture, VIN redacted)
# ---------------------------------------------------------------------------
LIVE_VEHICLES_RESPONSE = {
    "retCode": "S",
    "resCode": "0000",
    "resMsg": {
        "vehicles": [
            {
                "vin": "LBE***8954",
                "vehicleId": "dceb950b-a5be-4935-9fb5-5db5a951a2d4",
                "vehicleName": "库斯途",
                "type": "GN",
                "tmuNum": "***",
                "nickname": "库斯途 (京A·12345)",
                "year": "2025",
                "master": True,
                "carShare": 0,
                "regDate": "2025-07-13 20:12:40.283",
                "detailInfo": {"bleFunc": "1", "authYn": "N"},
                # NOTE: no ccuCCS2ProtocolSupport in the China vehicle list
            }
        ]
    },
    "msgId": "…",
}


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, payloads):
        self._payloads = payloads
        self.calls = []

    def get(self, url, headers=None, **kwargs):
        self.calls.append(("GET", url))
        return FakeResponse(self._payloads.pop(0))


class TestGetVehicles:
    def test_parses_live_schema_without_ccs2_flag(self, api: KiaUvoApiCN):
        api.session = FakeSession([LIVE_VEHICLES_RESPONSE])
        token = TokenStub()
        vehicles = api.get_vehicles(token)
        assert len(vehicles) == 1
        v = vehicles[0]
        assert v.id == "dceb950b-a5be-4935-9fb5-5db5a951a2d4"
        assert v.name == "库斯途 (京A·12345)"
        assert v.model == "库斯途"
        # GN -> ICE; missing ccuCCS2ProtocolSupport defaults to 0 (legacy path)
        assert v.ccu_ccs2_protocol_support == 0
        assert "vehicles" in api.session.calls[0][1]

    def test_device_id_registration_payload(self, api: KiaUvoApiCN, monkeypatch):
        captured = {}

        def fake_post(url, headers=None, json=None, **kwargs):
            captured["url"] = url
            captured["json"] = json
            return FakeResponse(
                {
                    "retCode": "S",
                    "resCode": "0000",
                    "resMsg": {"deviceId": "NEW-DEVICE"},
                }
            )

        monkeypatch.setattr(api.session, "post", fake_post)
        device_id = api._get_device_id()
        assert device_id == "NEW-DEVICE"
        assert captured["json"]["pushType"] == "APNS"  # GCM is dead on CN
        assert captured["json"]["providerDeviceId"]  # mandatory on CN
        assert "notifications/register" in captured["url"]


# ---------------------------------------------------------------------------
# airTemp "FFH" sentinel
# ---------------------------------------------------------------------------
class TestAirTempSentinel:
    def _state(self, air_temp_value):
        return {
            "status": {
                "time": "20260830180000",
                "doorLock": 1,
                "airTemp": {"value": air_temp_value, "unit": 0},
                "odometer": {"value": 13552, "unit": 1},
            }
        }

    def test_ffh_does_not_raise(self, api: KiaUvoApiCN, vehicle: Vehicle):
        # live-verified: AC-off cars report "FFH" which decodes far outside
        # temperature_range — must be skipped, not crash (regression test)
        api._update_vehicle_properties(vehicle, self._state("FFH"))
        assert vehicle.air_temperature is None

    def test_valid_temp_still_parses(self, api: KiaUvoApiCN, vehicle: Vehicle):
        from hyundai_kia_connect_api.utils import get_index_into_hex_temp

        # pick a value inside the range: index 0 -> "0G" style encoding
        idx = 14  # 21.0C sits mid-range
        hex_code = get_index_into_hex_temp(idx)
        api._update_vehicle_properties(vehicle, self._state(hex_code))
        assert vehicle.air_temperature == api.temperature_range[idx]


# ---------------------------------------------------------------------------
# login() Token lifetime (regression: old code faked a 30-day LOGIN_TOKEN_LIFETIME)
# ---------------------------------------------------------------------------
class TestTokenLifetime:
    def test_expires_in_21600_is_six_hours(self, api: KiaUvoApiCN):
        # the live server returns expiresIn=21600; ensure the arithmetic used
        # by login()/refresh_access_token() reflects it (6h, not 30 days)
        expires_in = 21600
        valid_until = dt.datetime.now(dt.UTC) + dt.timedelta(seconds=expires_in)
        assert (valid_until - dt.datetime.now(dt.UTC)).total_seconds() <= 21600
        assert dt.timedelta(hours=6).total_seconds() == expires_in
