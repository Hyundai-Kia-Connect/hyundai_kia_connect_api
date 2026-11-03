"""Tests for the CA gtothbyrftkn token refresh (Hyundai-Kia-Connect/kia_uvo#2025).

The CA mobile apps refresh with POST gtothbyrftkn instead of re-logging-in;
the library mirrors that flow and falls back to re-login when the refresh is
rejected. These tests lock in the outbound request shape (URL, body, headers)
and the fallback behaviour.
"""

import datetime as dt
from typing import Any
from unittest.mock import MagicMock

import pytest

from hyundai_kia_connect_api.ApiImpl import OTPRequest
from hyundai_kia_connect_api.KiaUvoApiCA import KiaUvoApiCA
from hyundai_kia_connect_api.Token import Token


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload

    @property
    def text(self):
        import json

        return json.dumps(self._payload)


def _refresh_ok_payload(token="RT2"):
    return {
        "responseHeader": {"responseCode": 0},
        "result": {
            "expireIn": 86400,
            "accessToken": "AT2",
            "refreshToken": token,
        },
    }


def _make_api() -> KiaUvoApiCA:
    api = KiaUvoApiCA(region=2, brand=1, language="en")
    api._sessions = MagicMock()
    return api


def _make_token(device_id="DEV", refresh_token="RT1", **kwargs) -> Token:
    return Token(
        username="user@test.com",
        password="pw",
        access_token="AT1",
        refresh_token=refresh_token,
        device_id=device_id,
        **kwargs,
    )


def _capture_posts(api: KiaUvoApiCA) -> list[dict[str, Any]]:
    posts = []
    for call in api._sessions.post.call_args_list:
        posts.append(
            {
                "url": call.args[0],
                "json": call.kwargs.get("json"),
                "headers": call.kwargs.get("headers"),
            }
        )
    return posts


def test_refresh_posts_gtothbyrftkn_shape():
    api = _make_api()
    api._sessions.post.return_value = _FakeResponse(_refresh_ok_payload())
    token = _make_token()
    refreshed = api.refresh_access_token(token)
    posts = _capture_posts(api)
    assert len(posts) == 1
    assert posts[0]["url"].endswith("gtothbyrftkn")
    assert posts[0]["json"] == {"refTokenData": "RT1"}
    headers = posts[0]["headers"]
    assert headers["from"] == "SPA"
    assert headers["Deviceid"] == "DEV"
    assert "accessToken" not in headers
    assert refreshed.access_token == "AT2"
    assert refreshed.refresh_token == "RT2"
    assert refreshed.device_id == "DEV"
    expected = dt.datetime.now(dt.UTC) + dt.timedelta(seconds=86400 - 60)
    assert abs((refreshed.valid_until - expected).total_seconds()) < 5


def test_refresh_rejected_falls_back_to_login():
    api = _make_api()
    rejected = {
        "responseHeader": {"responseCode": 1},
        "error": {"errorCode": "7470", "errorDesc": "refresh rejected"},
    }
    ok = {
        "responseHeader": {"responseCode": 0},
        "result": {
            "token": {
                "expireIn": 86400,
                "accessToken": "ATL",
                "refreshToken": "RTL",
            }
        },
    }
    api._sessions.post.side_effect = [_FakeResponse(rejected), _FakeResponse(ok)]
    refreshed = api.refresh_access_token(_make_token())
    posts = _capture_posts(api)
    assert posts[0]["url"].endswith("gtothbyrftkn")
    assert posts[1]["url"].endswith("v2/login")
    assert isinstance(refreshed, Token)
    assert refreshed.access_token == "ATL"


def test_refresh_non_json_falls_back_to_login():
    api = _make_api()
    api._sessions.post.side_effect = [
        _FakeResponse(None),
        _FakeResponse(
            {
                "responseHeader": {"responseCode": 0},
                "result": {
                    "token": {
                        "expireIn": 86400,
                        "accessToken": "A",
                        "refreshToken": "R",
                    }
                },
            }
        ),
    ]
    refreshed = api.refresh_access_token(_make_token())
    assert api._sessions.post.call_count == 2
    assert refreshed.access_token == "A"


def test_refresh_without_refresh_token_goes_straight_to_login():
    api = _make_api()
    api._sessions.post.return_value = _FakeResponse(
        {
            "responseHeader": {"responseCode": 0},
            "result": {
                "token": {"expireIn": 86400, "accessToken": "A", "refreshToken": "R"}
            },
        }
    )
    refreshed = api.refresh_access_token(_make_token(refresh_token=None))
    posts = _capture_posts(api)
    assert posts[0]["url"].endswith("v2/login")
    assert refreshed.access_token == "A"


def test_refresh_fallback_login_can_return_otp_request():
    api = _make_api()
    rejected = {
        "responseHeader": {"responseCode": 1},
        "error": {"errorCode": "7470", "errorDesc": "refresh rejected"},
    }
    otp = {
        "responseHeader": {"responseCode": 1},
        "error": {"errorCode": "7110", "errorDesc": "otp required"},
    }
    selverifmeth = {
        "responseHeader": {"responseCode": 0},
        "result": {"userInfoUuid": "UU", "emailList": ["user@test.com"]},
    }
    api._sessions.post.side_effect = [
        _FakeResponse(rejected),
        _FakeResponse(otp),
        _FakeResponse(selverifmeth),
    ]
    result = api.refresh_access_token(_make_token())
    assert isinstance(result, OTPRequest)


@pytest.mark.parametrize(
    "payload",
    [
        {"responseHeader": {}},
        {"responseHeader": {"responseCode": 0}, "result": {}},
    ],
)
def test_refresh_malformed_payloads_fall_back(payload):
    api = _make_api()
    api._sessions.post.side_effect = [
        _FakeResponse(payload),
        _FakeResponse(
            {
                "responseHeader": {"responseCode": 0},
                "result": {
                    "token": {
                        "expireIn": 86400,
                        "accessToken": "A",
                        "refreshToken": "R",
                    }
                },
            }
        ),
    ]
    refreshed = api.refresh_access_token(_make_token())
    assert api._sessions.post.call_count == 2
    assert refreshed.access_token == "A"
