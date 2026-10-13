"""Tests for the EU CCI X-Fingerprint helper (cci_fingerprint.py) and the
per-brand token-exchange paths (live-verified 2026-10-13, kia_uvo #1888):
Hyundai EU is validation-gated, so /auth/v1/token + X-Fingerprint goes first;
Kia/Genesis still answer the legacy domain/api exchange. On a failed primary
the other path runs before raising."""

import json
from unittest.mock import MagicMock, patch
from urllib.parse import urlparse

import pytest

from hyundai_kia_connect_api.cci_fingerprint import (
    build_fp_headers,
    compute_x_fingerprint,
)
from hyundai_kia_connect_api.HyundaiCciApiEU import HyundaiCciApiEU
from hyundai_kia_connect_api.KiaCciApiEU import KiaCciApiEU
from hyundai_kia_connect_api.KiaUvoApiEU import KiaUvoApiEU

GOLDEN_TS = "1736700000000"
GOLDEN_FP = "aef1b5735838a04aa83a0b5e63b87beb95ce50cf89b30d42405f7db20f0070c7"


def _assert_kia_url(call_arg: object, path: str) -> None:
    """Compare URL components; prefix-substring asserts trip CodeQL
    incomplete-URL-substring-sanitization."""
    parsed = urlparse(call_arg)
    assert (parsed.scheme, parsed.netloc, parsed.path) == (
        "https",
        "cci-api-eu.kia.com",
        path,
    )


GATE_BODY = '{"code":9013,"message":"ctn_externalservice_validation_error"}'


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


def _resp(status: int, text: str) -> MagicMock:
    resp = MagicMock(status_code=status, text=text)
    resp.json.return_value = json.loads(text) if status in (200, 201) else {}
    return resp


# ── region 1 (KiaUvoApiEU) ──────────────────────────────────────


def _eu_api(brand: int = 2) -> KiaUvoApiEU:
    return KiaUvoApiEU(region=1, brand=brand, language="en")


@patch("hyundai_kia_connect_api.KiaUvoApiEU.requests.post")
def test_region1_hyundai_exchange_fp_primary(mock_post):
    mock_post.return_value = _resp(200, '{"accessToken":"cci-at"}')
    resp = _eu_api(brand=2)._exchange_auth_code_for_cci_tokens("dev-1", "CODE123")
    assert resp.json()["accessToken"] == "cci-at"
    assert mock_post.call_count == 1
    assert mock_post.call_args.args[0].endswith("/auth/v1/token")
    sent = mock_post.call_args.kwargs["headers"]
    assert sent["X-Fingerprint"]
    assert sent["X-Timestamp"]
    assert sent["client-name"] == "HyundaiEU"
    assert sent["client-id"] == "com.hyundai.oneapp.eu"


@patch("hyundai_kia_connect_api.KiaUvoApiEU.requests.post")
def test_region1_hyundai_exchange_falls_back_to_legacy(mock_post):
    mock_post.side_effect = [
        _resp(400, GATE_BODY),
        _resp(200, '{"accessToken":"legacy-at"}'),
    ]
    resp = _eu_api(brand=2)._exchange_auth_code_for_cci_tokens("dev-1", "CODE123")
    assert resp.json()["accessToken"] == "legacy-at"
    assert mock_post.call_count == 2
    assert mock_post.call_args_list[0].args[0].endswith("/auth/v1/token")
    assert mock_post.call_args_list[1].args[0].endswith("/domain/api/v1/auth/token")
    assert mock_post.call_args_list[1].kwargs["headers"].get("X-Fingerprint") is None


@patch("hyundai_kia_connect_api.KiaUvoApiEU.requests.post")
def test_region1_kia_exchange_legacy_primary(mock_post):
    mock_post.return_value = _resp(200, '{"accessToken":"kia-at"}')
    resp = _eu_api(brand=1)._exchange_auth_code_for_cci_tokens("dev-1", "CODE123")
    assert resp.json()["accessToken"] == "kia-at"
    assert mock_post.call_count == 1
    assert mock_post.call_args.args[0].endswith("/domain/api/v1/auth/token")
    _assert_kia_url(mock_post.call_args.args[0], "/domain/api/v1/auth/token")


@patch("hyundai_kia_connect_api.KiaUvoApiEU.requests.post")
def test_region1_kia_exchange_fallback_to_fp(mock_post):
    mock_post.side_effect = [
        _resp(400, GATE_BODY),
        _resp(200, '{"accessToken":"kia-fp-at"}'),
    ]
    resp = _eu_api(brand=1)._exchange_auth_code_for_cci_tokens("dev-1", "CODE123")
    assert resp.json()["accessToken"] == "kia-fp-at"
    _assert_kia_url(mock_post.call_args_list[1].args[0], "/auth/v1/token")
    sent = mock_post.call_args_list[1].kwargs["headers"]
    assert sent["client-name"] == "KiaEU"
    assert sent["client-id"] == "com.kia.oneapp.eu"


@patch("hyundai_kia_connect_api.KiaUvoApiEU.requests.post")
def test_region1_exchange_both_paths_fail_raises(mock_post):
    from hyundai_kia_connect_api.exceptions import AuthenticationError

    mock_post.side_effect = [_resp(400, GATE_BODY), _resp(400, GATE_BODY)]
    with pytest.raises(AuthenticationError):
        _eu_api(brand=2)._exchange_auth_code_for_cci_tokens("dev-1", "CODE123")


def test_region1_fp_client_identities():
    assert _eu_api(brand=1)._cci_fp_headers("d")["client-name"] == "KiaEU"
    assert _eu_api(brand=2)._cci_fp_headers("d")["client-name"] == "HyundaiEU"
    assert _eu_api(brand=3)._cci_fp_headers("d")["client-name"] == "GenesisEU"
    assert _eu_api(brand=3)._cci_fp_headers("d")["client-id"] == (
        "com.genesis.oneapp.eu"
    )


# ── region 9 (HyundaiCciApiEU / KiaCciApiEU, shared GspaApiEU base) ──


@patch("hyundai_kia_connect_api.GspaApiEU.requests.post")
def test_region9_hyundai_exchange_fp_primary(mock_post):
    mock_post.return_value = _resp(200, '{"accessToken":"r9-at"}')
    cci = HyundaiCciApiEU(
        region=9, brand=2, language="en"
    )._exchange_auth_code_for_cci_tokens("dev-1", "CODE123")
    assert cci["accessToken"] == "r9-at"
    assert mock_post.call_count == 1
    assert mock_post.call_args.args[0].endswith("/auth/v1/token")
    assert mock_post.call_args.kwargs["headers"]["X-Fingerprint"]


@patch("hyundai_kia_connect_api.GspaApiEU.requests.post")
def test_region9_hyundai_exchange_falls_back_to_legacy(mock_post):
    mock_post.side_effect = [
        _resp(400, GATE_BODY),
        _resp(200, '{"accessToken":"r9-legacy"}'),
    ]
    cci = HyundaiCciApiEU(
        region=9, brand=2, language="en"
    )._exchange_auth_code_for_cci_tokens("dev-1", "CODE123")
    assert cci["accessToken"] == "r9-legacy"
    assert mock_post.call_args_list[1].args[0].endswith("/domain/api/v1/auth/token")


@patch("hyundai_kia_connect_api.GspaApiEU.requests.post")
def test_region9_kia_exchange_legacy_primary(mock_post):
    mock_post.return_value = _resp(200, '{"accessToken":"r9k-at"}')
    cci = KiaCciApiEU(
        region=9, brand=1, language="en"
    )._exchange_auth_code_for_cci_tokens("dev-1", "CODE123")
    assert cci["accessToken"] == "r9k-at"
    assert mock_post.call_count == 1
    assert mock_post.call_args.args[0].endswith("/domain/api/v1/auth/token")
    _assert_kia_url(mock_post.call_args.args[0], "/domain/api/v1/auth/token")


@patch("hyundai_kia_connect_api.GspaApiEU.requests.post")
def test_region9_kia_exchange_fallback_to_fp(mock_post):
    mock_post.side_effect = [
        _resp(400, GATE_BODY),
        _resp(200, '{"accessToken":"r9k-fp"}'),
    ]
    cci = KiaCciApiEU(
        region=9, brand=1, language="en"
    )._exchange_auth_code_for_cci_tokens("dev-1", "CODE123")
    assert cci["accessToken"] == "r9k-fp"
    _assert_kia_url(mock_post.call_args_list[1].args[0], "/auth/v1/token")
    assert mock_post.call_args_list[1].kwargs["headers"]["client-name"] == "KiaEU"
