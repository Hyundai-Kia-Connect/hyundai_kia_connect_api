"""Tests for the EU CCI X-Fingerprint helper (cci_fingerprint.py), the
per-brand token-exchange paths, and the signed token-refresh (live-verified
2026-10-14, kia_uvo #1888 / #1938 / #1939): all EU OneApp /auth endpoints
require the X-Fingerprint identity headers; the legacy domain/api exchange
and refresh run as fallbacks on a failed primary."""

import json
from unittest.mock import MagicMock, patch
from urllib.parse import urlparse

import pytest

from hyundai_kia_connect_api.cci_fingerprint import (
    build_fp_headers,
    compute_x_fingerprint,
)
from hyundai_kia_connect_api.KiaUvoApiEU import KiaUvoApiEU

# Live-pinned regression vectors: an empty-body exchange that answered 200
# with this exact timestamp and fingerprint (Kia 2026-10-14 vector shared
# with an independent implementation for cross-checking).
KIA_GOLDEN_TS = "1700000000123"
KIA_GOLDEN_FP = "0a3ceccc83823ec0d2c5d39a15dbf722c813e6da48800a0156a7a8459db63337"
HYUNDAI_GOLDEN_TS = "1792000084084"
HYUNDAI_GOLDEN_FP = "df04e3dadd52288ce5b280b368af261272556b5360adbc90a817a167526e2946"

_KIA_IDENTITY = {
    "client-id": "com.kia.oneapp.eu",
    "client-name": "KiaEU",
    "client-version": "1.4.0",
    "client-os-code": "android",
    "client-os-version": "14",
}


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


def _fp_headers(ts: str = HYUNDAI_GOLDEN_TS) -> dict[str, str]:
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


def test_compute_x_fingerprint_matches_pinned_kia_vector():
    headers = dict(_KIA_IDENTITY)
    headers["X-Timestamp"] = KIA_GOLDEN_TS
    assert compute_x_fingerprint(headers, 1) == KIA_GOLDEN_FP


def test_compute_x_fingerprint_matches_live_hyundai_vector():
    assert compute_x_fingerprint(_fp_headers(), 2) == HYUNDAI_GOLDEN_FP


def test_compute_x_fingerprint_strips_java_ascii_whitespace():
    headers = dict(_KIA_IDENTITY)
    headers["X-Timestamp"] = KIA_GOLDEN_TS
    bare = compute_x_fingerprint(headers, 1)
    assert compute_x_fingerprint(headers, 1, " \t\r\n") == bare


def test_compute_x_fingerprint_keeps_unicode_whitespace():
    headers = dict(_KIA_IDENTITY)
    headers["X-Timestamp"] = KIA_GOLDEN_TS
    bare = compute_x_fingerprint(headers, 1)
    assert compute_x_fingerprint(headers, 1, " ") != bare


def test_compute_x_fingerprint_signs_body():
    headers = _fp_headers()
    bare = compute_x_fingerprint(headers, 2)
    with_body = compute_x_fingerprint(headers, 2, '{"a":1}')
    assert with_body != bare


def test_compute_x_fingerprint_differs_per_brand():
    headers = _fp_headers()
    assert compute_x_fingerprint(headers, 1) != compute_x_fingerprint(headers, 2)


def test_build_fp_headers_matches_verified_identity():
    headers = _fp_headers()
    assert headers["client-version"] == "1.4.0"
    assert headers["client-os-code"] == "android"
    assert headers["client-os-version"] == "14"
    assert headers["client-device-model"] == "SM-S918B"
    assert headers["client-notification-provider-type"] == "FCM"
    assert headers["User-Agent"] == "okhttp/4.12.0"
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
def test_region1_kia_exchange_fp_primary(mock_post):
    mock_post.return_value = _resp(200, '{"accessToken":"kia-at"}')
    resp = _eu_api(brand=1)._exchange_auth_code_for_cci_tokens("dev-1", "CODE123")
    assert resp.json()["accessToken"] == "kia-at"
    assert mock_post.call_count == 1
    sent = mock_post.call_args.kwargs["headers"]
    assert sent["client-name"] == "KiaEU"
    assert sent["client-id"] == "com.kia.oneapp.eu"


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


# ── signed token-refresh ────────────────────────────────────────


def _refresh_token() -> object:
    from hyundai_kia_connect_api.Token import Token

    return Token(
        username="u",
        password="p",
        access_token="Bearer ccs-at",
        refresh_token="cci-rt",
        device_id="dev-1",
        pin=None,
        cci_access_token="cci-at",
        exchangeable_token="ex-at",
        exchangeable_refresh_token="ex-rt",
        non_ccs_token="non-ccs-token",
        non_ccs_refresh_token="non-ccs-rt",
        id_token="id-t",
    )


@patch("hyundai_kia_connect_api.KiaUvoApiEU.requests.post")
@patch.object(KiaUvoApiEU, "_exchange_ccs_token", return_value=("Bearer ccs", None))
def test_region1_refresh_fp_primary_signed_over_body(_mock_ccs, mock_post):
    mock_post.return_value = _resp(200, _refresh_response_json())
    api = _eu_api(brand=2)
    api._refresh_cci_token(_refresh_token())  # type: ignore[arg-type]
    assert mock_post.call_count == 1
    assert mock_post.call_args.args[0].endswith("/auth/v2/token-refresh")
    sent_headers = mock_post.call_args.kwargs["headers"]
    assert sent_headers["client-os-code"] == "android"
    assert (
        compute_x_fingerprint(sent_headers, 2, mock_post.call_args.kwargs["data"])
        == sent_headers["X-Fingerprint"]
    )


@patch("hyundai_kia_connect_api.KiaUvoApiEU.requests.post")
@patch.object(KiaUvoApiEU, "_exchange_ccs_token", return_value=("Bearer ccs", None))
def test_region1_refresh_falls_back_to_legacy(_mock_ccs, mock_post):
    mock_post.side_effect = [
        _resp(400, GATE_BODY),
        _resp(200, _refresh_response_json()),
    ]
    api = _eu_api(brand=2)
    token = _refresh_token()
    api._refresh_cci_token(token)  # type: ignore[arg-type]
    assert mock_post.call_count == 2
    legacy_call = mock_post.call_args_list[1]
    assert legacy_call.args[0].endswith("/domain/api/v2/auth/token-refresh")
    assert "idToken" in legacy_call.kwargs["json"]


def _refresh_response_json() -> str:
    return json.dumps(
        {
            "accessToken": "cci-at-new",
            "refreshToken": "cci-rt-new",
            "nonCcsToken": "non-ccs-token-new",
            "exchangeableAccessToken": "ex-at-new",
            "exchangeableRefreshToken": "ex-rt-new",
            "nonCcsRefreshToken": "non-ccs-rt-new",
            "idToken": "id-t-new",
        }
    )
