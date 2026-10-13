"""Tests for the EU CCI X-Fingerprint helper (cci_fingerprint.py) and the
9013 validation-gate fallback on the token exchange and token refresh paths
(live-verified 2026-10-13, kia_uvo #1888)."""

import json
from unittest.mock import MagicMock, patch

import pytest

from hyundai_kia_connect_api.cci_fingerprint import (
    build_fp_headers,
    client_name_for_brand,
    compute_x_fingerprint,
    is_fingerprint_gate,
)
from hyundai_kia_connect_api.HyundaiCciApiEU import HyundaiCciApiEU
from hyundai_kia_connect_api.KiaUvoApiEU import KiaUvoApiEU

GOLDEN_TS = "1736700000000"
GOLDEN_FP = "aef1b5735838a04aa83a0b5e63b87beb95ce50cf89b30d42405f7db20f0070c7"


def _fp_headers(ts: str = GOLDEN_TS) -> dict[str, str]:
    headers = build_fp_headers(
        package_id="com.hyundai.oneapp.eu",
        client_name="HyundaiEU",
        device_id="dev-1",
        locale="PL",
        timezone="+02:00",
        brand_label="Hyundai",
    )
    headers["X-Timestamp"] = ts
    return headers


# ── cci_fingerprint module ──────────────────────────────────────


def test_compute_x_fingerprint_matches_live_verified_golden():
    assert compute_x_fingerprint(_fp_headers()) == GOLDEN_FP


def test_compute_x_fingerprint_uses_body_when_present():
    bare = compute_x_fingerprint(_fp_headers())
    with_body = compute_x_fingerprint(_fp_headers(), '{"a":1}')
    assert with_body != bare


def test_client_name_for_brand():
    assert client_name_for_brand(2) == "HyundaiEU"  # 2 = Hyundai in BRANDS
    assert client_name_for_brand(1) == "KiaEU"
    assert client_name_for_brand(3) == "GenesisEU"


def test_build_fp_headers_matches_verified_identity():
    headers = _fp_headers()
    assert headers["client-version"] == "1.4.1"
    assert headers["client-os-version"] == "27.2.0"
    assert headers["User-Agent"] == (
        "HyundaiEU/1.4.1 (com.hyundai.oneapp.eu; build:1675; iOS 27.2.0) "
        "Alamofire/5.12.0"
    )
    assert headers["X-Brand"] == "Hyundai"
    assert headers["X-Region"] == "eu"
    assert headers["App-Request-Id"]


def test_is_fingerprint_gate():
    assert is_fingerprint_gate(
        MagicMock(status_code=400, text='{"code":9013,"message":"x"}')
    )
    assert not is_fingerprint_gate(MagicMock(status_code=200, text="{}"))
    assert not is_fingerprint_gate(
        MagicMock(status_code=400, text='{"code":4002,"message":"x"}')
    )


def _resp(status: int, text: str) -> MagicMock:
    resp = MagicMock(status_code=status, text=text)
    resp.json.return_value = json.loads(text) if status in (200, 201) else {}
    return resp


# ── GspaApiEU fallback (region 9 classes) ───────────────────────


def _cci_api() -> HyundaiCciApiEU:
    return HyundaiCciApiEU(region=1, brand=2, language="en")


@patch("hyundai_kia_connect_api.GspaApiEU.requests.post")
def test_gspa_exchange_falls_back_on_9013(mock_post):
    mock_post.side_effect = [
        _resp(400, '{"code":9013,"message":"ctn_externalservice_validation_error"}'),
        _resp(200, '{"accessToken":"cci-at","refreshToken":"cci-rt"}'),
    ]
    cci = _cci_api()._exchange_auth_code_for_cci_tokens("dev-1", "CODE123")
    assert cci["accessToken"] == "cci-at"
    assert mock_post.call_count == 2
    first_url = mock_post.call_args_list[0].args[0]
    second_url = mock_post.call_args_list[1].args[0]
    assert first_url.endswith("/domain/api/v1/auth/token")
    assert second_url.endswith("/auth/v1/token")
    sent = mock_post.call_args_list[1].kwargs["headers"]
    assert sent["X-Fingerprint"]
    assert sent["X-Timestamp"]
    assert sent["client-version"] == "1.4.1"


@patch("hyundai_kia_connect_api.GspaApiEU.requests.post")
def test_gspa_exchange_non_gate_failure_propagates(mock_post):
    mock_post.return_value = _resp(500, '{"code":5001,"message":"boom"}')
    from hyundai_kia_connect_api.exceptions import AuthenticationError

    with pytest.raises(AuthenticationError):
        _cci_api()._exchange_auth_code_for_cci_tokens("dev-1", "CODE123")
    assert mock_post.call_count == 1


# ── KiaUvoApiEU fallback (region 1, the kia_uvo prod path) ──────


def _eu_api(brand: int = 2) -> KiaUvoApiEU:
    return KiaUvoApiEU(region=1, brand=brand, language="en")


@patch("hyundai_kia_connect_api.KiaUvoApiEU.requests.post")
def test_kiauvo_exchange_falls_back_on_9013(mock_post):
    mock_post.side_effect = [
        _resp(400, '{"code":9013,"message":"External service validation error"}'),
        _resp(200, '{"accessToken":"cci-at","refreshToken":"cci-rt"}'),
    ]
    api = _eu_api()
    resp = api._exchange_auth_code_for_cci_tokens("dev-1", "CODE123")
    assert resp.json()["accessToken"] == "cci-at"
    assert mock_post.call_count == 2
    assert mock_post.call_args_list[0].args[0].endswith("/domain/api/v1/auth/token")
    assert mock_post.call_args_list[1].args[0].endswith("/auth/v1/token")
    sent = mock_post.call_args_list[1].kwargs["headers"]
    assert sent["X-Fingerprint"]
    assert sent["client-name"] == "HyundaiEU"


def test_kiauvo_fp_headers_use_verified_identity():
    sent = _eu_api()._cci_fp_headers("dev-1")
    assert sent["client-id"] == "com.hyundai.oneapp.eu"
    assert sent["client-name"] == "HyundaiEU"
    assert sent["client-version"] == "1.4.1"
    assert sent["User-Agent"] == (
        "HyundaiEU/1.4.1 (com.hyundai.oneapp.eu; build:1675; iOS 27.2.0) "
        "Alamofire/5.12.0"
    )


def test_kiauvo_fp_client_name_kia_brand():
    assert _eu_api(brand=1)._cci_fp_headers("dev-1")["client-name"] == "KiaEU"


# ── Kia brand (region 9 KiaCciApiEU + region 1 KiaUvoApiEU) ─────


@patch("hyundai_kia_connect_api.GspaApiEU.requests.post")
def test_kia_cci_exchange_falls_back_on_9013(mock_post):
    from hyundai_kia_connect_api.KiaCciApiEU import KiaCciApiEU

    mock_post.side_effect = [
        _resp(400, '{"code":9013,"message":"ctn_externalservice_validation_error"}'),
        _resp(200, '{"accessToken":"kia-at","refreshToken":"kia-rt"}'),
    ]
    api = KiaCciApiEU(region=9, brand=1, language="en")
    cci = api._exchange_auth_code_for_cci_tokens("dev-1", "CODE123")
    assert cci["accessToken"] == "kia-at"
    assert (
        mock_post.call_args_list[1]
        .args[0]
        .startswith("https://cci-api-eu.kia.com/auth/v1/token")
    )
    sent = mock_post.call_args_list[1].kwargs["headers"]
    assert sent["client-name"] == "KiaEU"
    assert sent["client-id"] == "com.kia.oneapp.eu"


def test_kia_cci_fp_headers_use_verified_identity():
    from hyundai_kia_connect_api.KiaCciApiEU import KiaCciApiEU

    sent = KiaCciApiEU(region=9, brand=1, language="en")._cci_fp_headers("dev-1")
    assert sent["client-name"] == "KiaEU"
    assert sent["client-id"] == "com.kia.oneapp.eu"
    assert sent["User-Agent"] == (
        "KiaEU/1.4.1 (com.kia.oneapp.eu; build:1675; iOS 27.2.0) Alamofire/5.12.0"
    )


def test_kiauvo_fp_client_name_genesis_brand():
    sent = _eu_api(brand=3)._cci_fp_headers("dev-1")
    assert sent["client-name"] == "GenesisEU"
