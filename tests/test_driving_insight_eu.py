"""Driving Insight (DI) client unit tests (EU CCI)."""

from __future__ import annotations

import datetime as dt
import json
from unittest.mock import patch

import requests

from hyundai_kia_connect_api.Token import Token
from hyundai_kia_connect_api.Vehicle import Vehicle


def _api():
    from hyundai_kia_connect_api import KiaCciApiEU

    return KiaCciApiEU(9, 1, "en")


def _token() -> Token:
    return Token(
        username="u",
        password="p",
        access_token="Bearer access",
        device_id="0123456789abcdef0123456789abcdef",
        cci_access_token="cci-token",
    )


def _answered(body: dict, status: int = 200):
    def fake(url, **kwargs):  # requests.get
        response = requests.Response()
        response.status_code = status
        response.url = url
        response._content = b"" if body is None else json.dumps(body).encode()
        return response

    return fake


def test_di_request_returns_data_on_success_envelope():
    api = _api()
    vehicle = Vehicle(id="VEHICLE-1", name="EV", VIN="TESTVIN0000000001")
    body = {
        "transactionId": "t1",
        "status": "success",
        "resultCode": 200000,
        "resultMessage": "success",
        "data": {"supportedFeatures": ["TRIPS"]},
    }
    token = _token()
    token.di_access_token = "DI"
    token.di_token_valid_until = dt.datetime.now(dt.UTC) + dt.timedelta(hours=1)
    with patch(
        "hyundai_kia_connect_api.driving_insight_eu.requests.get", _answered(body)
    ):
        assert api._di_request(token, vehicle, "/vehicle/X") == {
            "supportedFeatures": ["TRIPS"]
        }


def test_di_request_logs_unavailable_and_returns_none():
    api = _api()
    vehicle = Vehicle(id="VEHICLE-1", name="EV", VIN="TESTVIN0000000001")
    token = _token()
    token.di_access_token = "DI"
    token.di_token_valid_until = dt.datetime.now(dt.UTC) + dt.timedelta(hours=1)
    body = {
        "status": "fail",
        "resultCode": 400000,
        "resultMessage": "nope",
        "data": None,
    }
    with patch(
        "hyundai_kia_connect_api.driving_insight_eu.requests.get", _answered(body)
    ):
        assert api._di_request(token, vehicle, "/tire/bound") is None


def test_ensure_di_token_exchanges_and_caches():
    api = _api()
    token = _token()
    with patch.object(
        api,
        "_exchange_ccs_token",
        return_value=("DIACCESS", dt.datetime.now(dt.UTC) + dt.timedelta(hours=1)),
    ) as exchange:
        assert api._ensure_di_token(token) is True
        assert token.di_access_token == "DIACCESS"
        exchange.assert_called_once()
        assert exchange.call_args.kwargs.get("service_type") == "DI"


def test_ensure_di_token_false_on_exchange_failure():
    api = _api()
    token = _token()
    with patch.object(api, "_exchange_ccs_token", side_effect=Exception("down")):
        assert api._ensure_di_token(token) is False
    assert token.di_access_token is None
