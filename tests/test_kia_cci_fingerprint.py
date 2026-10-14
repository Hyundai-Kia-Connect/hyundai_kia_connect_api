"""Offline CCI signing and login/refresh regression tests; no live credentials."""

import datetime as dt
import importlib
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from hyundai_kia_connect_api.kia_cci_fingerprint import _signature
from hyundai_kia_connect_api.KiaUvoApiEU import KiaUvoApiEU
from hyundai_kia_connect_api.Token import Token

eu = importlib.import_module("hyundai_kia_connect_api.KiaUvoApiEU")
signer = importlib.import_module("hyundai_kia_connect_api.kia_cci_fingerprint")


@pytest.fixture
def api():
    return KiaUvoApiEU(region=1, brand=1, language="en")


@pytest.fixture
def calls(monkeypatch):
    recorded = []

    def post(url, **kwargs):
        recorded.append((url, kwargs))
        response = SimpleNamespace(status_code=200, text="", headers={})
        response.json = lambda: {
            "accessToken": "test-only-access",
            "refreshToken": "test-only-refresh",
            "expiresTime": 600,
        }
        return response

    monkeypatch.setattr(eu.requests, "post", post)
    monkeypatch.setattr(signer.time, "time", lambda: 1700000000.123)
    return recorded


def assert_signed(kwargs):
    headers = kwargs["headers"]
    assert headers["X-Timestamp"] == "1700000000123"
    assert headers["X-Fingerprint"] == _signature(
        headers, kwargs["data"], headers["X-Timestamp"]
    )
    assert headers["client-os-code"] == "android"
    assert kwargs["timeout"] == (5, 30)


def test_recovered_app_empty_body_vector():
    headers = {
        "client-id": "com.kia.oneapp.eu",
        "client-name": "KiaEU",
        "client-version": "1.4.0",
        "client-os-code": "android",
        "client-os-version": "14",
    }
    assert (
        _signature(headers, b"", 1700000000123)
        == "0a3ceccc83823ec0d2c5d39a15dbf722c813e6da48800a0156a7a8459db63337"
    )
    assert (
        _signature(headers, b" \t\r\n", 1700000000123)
        == "0a3ceccc83823ec0d2c5d39a15dbf722c813e6da48800a0156a7a8459db63337"
    )
    assert (
        _signature(headers, "\u00a0", 1700000000123)
        != "0a3ceccc83823ec0d2c5d39a15dbf722c813e6da48800a0156a7a8459db63337"
    )


def test_login_routes_signed_code_exchange(api, calls, monkeypatch):
    session = MagicMock()
    session.get.side_effect = [
        SimpleNamespace(text="", url="https://idpconnect-eu.kia.com/authorize"),
        SimpleNamespace(
            status_code=200,
            json=lambda: {"retValue": {"kid": "test-only", "n": "AQ", "e": "AQ"}},
        ),
    ]
    session.post.return_value = SimpleNamespace(
        status_code=302,
        headers={"location": "https://oneapp.kia.com/redirect?code=test-only-code"},
    )
    monkeypatch.setattr(eu, "ApiImplSession", lambda: session)
    monkeypatch.setattr(eu.RSA, "construct", lambda key: key)
    monkeypatch.setattr(
        eu.PKCS1_v1_5,
        "new",
        lambda key: SimpleNamespace(encrypt=lambda password: b"test-only"),
    )
    monkeypatch.setattr(
        api,
        "_exchange_ccs_token",
        lambda *args: ("test-only-ccs", dt.datetime.now(dt.UTC)),
    )
    result = api._login_with_password_cci(
        "test@example.invalid", "test-only-password", "test-only-device"
    )
    assert result["access_token"] == "Bearer test-only-ccs"
    url, kwargs = calls[0]
    assert url == "https://cci-api-eu.kia.com/auth/v1/token"
    assert kwargs["params"] == {"code": "test-only-code"}
    assert kwargs["headers"]["client-device-id"] == "test-only-device"
    assert_signed(kwargs)


def test_ccs_exchange_preserves_token_fields(api, calls):
    before = dt.datetime.now(dt.UTC)
    token, valid_until = api._exchange_ccs_token(
        "test-only-device",
        "Bearer test-only-cci",
        "test-only-non-ccs",
        "test-only-exchange",
    )
    assert token == "test-only-access"
    assert (
        before + dt.timedelta(seconds=590)
        < valid_until
        < dt.datetime.now(dt.UTC) + dt.timedelta(seconds=610)
    )
    url, kwargs = calls[0]
    assert url == "https://cci-api-eu.kia.com/domain/api/v1/auth/token-exchange"
    assert kwargs["params"] == {"serviceType": "CCS"}
    assert kwargs["headers"]["authorization"] == "Bearer test-only-cci"
    assert kwargs["headers"]["Authentication"] == "test-only-non-ccs"
    assert kwargs["headers"]["exchangeable-token"] == "test-only-exchange"
    assert_signed(kwargs)


def test_refresh_signs_serialized_complete_token_set(api, calls, monkeypatch):
    old = Token(
        username="test@example.invalid",
        password="test-only",
        access_token="Bearer test-only-ccs",
        refresh_token="test-only-old-refresh",
        device_id="test-only-device",
        valid_until=dt.datetime.now(dt.UTC),
        pin="0000",
        cci_access_token="Bearer test-only-cci",
        exchangeable_token="test-only-exchange",
        exchangeable_refresh_token="test-only-exchange-refresh",
        non_ccs_token="test-only-non-ccs",
        non_ccs_refresh_token="test-only-non-ccs-refresh",
        id_token="test-only-id",
    )
    monkeypatch.setattr(
        api,
        "_exchange_ccs_token",
        lambda *args: ("test-only-new-ccs", dt.datetime.now(dt.UTC)),
    )
    refreshed = api._refresh_cci_token(old)
    assert refreshed.refresh_token == "test-only-refresh"
    assert refreshed.access_token == "Bearer test-only-new-ccs"
    assert refreshed.exchangeable_refresh_token == old.exchangeable_refresh_token
    url, kwargs = calls[0]
    assert url == "https://cci-api-eu.kia.com/domain/api/v2/auth/token-refresh"
    assert "json" not in kwargs
    assert json.loads(kwargs["data"]) == {
        "accessToken": "test-only-cci",
        "refreshToken": "test-only-old-refresh",
        "exchangeableAccessToken": "test-only-exchange",
        "exchangeableRefreshToken": "test-only-exchange-refresh",
        "nonCcsToken": "test-only-non-ccs",
        "nonCcsRefreshToken": "test-only-non-ccs-refresh",
        "idToken": "test-only-id",
    }
    assert int(kwargs["headers"]["Content-Length"]) == len(kwargs["data"])
    assert kwargs["headers"]["Content-Type"] == "application/json"
    assert_signed(kwargs)


@pytest.mark.parametrize("brand,host", [(2, "hyundai"), (3, "genesis")])
def test_other_brand_cci_requests_are_unchanged(calls, brand, host):
    api = KiaUvoApiEU(region=1, brand=brand, language="en")
    url = f"https://cci-api-eu.{host}.com/domain/api/v1/auth/token"
    kwargs = {
        "json": {"sample": "test-only"},
        "headers": {"client-os-code": "ios"},
        "timeout": 15,
    }
    api._post_signed_cci(url, **kwargs)
    assert calls == [(url, kwargs)]


@pytest.mark.parametrize(
    "url",
    [
        "https://idpconnect-eu.kia.com/auth/signin",
        "https://gspa-ccs-eu.kia.com/gspa/v1/vehicles",
        "https://cci-api-eu.kia.com/unrelated",
        "http://cci-api-eu.kia.com/auth/v1/token",
        "https://cci-api-eu.kia.com/domain/api/v1/auth/token",
    ],
)
def test_request_scope_does_not_expand(api, calls, url):
    kwargs = {
        "headers": {"x-test": "test-only"},
        "json": {"sample": "test-only"},
        "timeout": 15,
    }
    api._post_signed_cci(url, **kwargs)
    assert calls == [(url, kwargs)]
