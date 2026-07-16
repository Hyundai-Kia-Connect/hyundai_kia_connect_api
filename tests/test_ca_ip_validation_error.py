"""Tests for KiaUvoApiCA IP-bound token handling (kia_uvo#1715).

CA rejects an access token with errorCode 7606 ("Access Token IP Validation
failed") when the caller's IP no longer matches the IP the token was issued
from (e.g. an ISP lease renewal around a nightly Home Assistant restart).
test_token() previously only recognized 7403/7602 as invalidating errors, so
7606 was reported as a valid token. check_and_refresh_token() then skipped
the refresh/re-login path and fell through to initialize_vehicles(), which
made the same doomed call again -- this time via get_vehicles(), which does
not tolerate the error and raises an unhandled APIError, crashing config
entry setup instead of transparently re-authenticating.
"""

import datetime as dt
from unittest.mock import MagicMock, patch

from hyundai_kia_connect_api.KiaUvoApiCA import KiaUvoApiCA
from hyundai_kia_connect_api.Token import Token
from hyundai_kia_connect_api.VehicleManager import VehicleManager


class _FakeResponse:
    """Minimal stand-in for requests.Response used by CA API calls."""

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload

    @property
    def text(self):
        import json

        return json.dumps(self._payload)


def _ip_validation_error_payload():
    return {
        "error": {
            "errorCode": "7606",
            "errorDesc": "Access Token IP Validation failed.",
        },
        "responseHeader": {"responseCode": 1},
    }


def _login_ok_payload():
    return {
        "responseHeader": {"responseCode": 0},
        "result": {
            "token": {
                "expireIn": 86400,
                "accessToken": "NEW-AT",
                "refreshToken": "NEW-RT",
            }
        },
    }


def _vehicles_ok_payload():
    return {
        "responseHeader": {"responseCode": 0},
        "result": {
            "vehicles": [
                {
                    "vehicleId": "vehicle-1",
                    "nickName": "My Car",
                    "modelName": "IONIQ 5",
                    "modelYear": "2024",
                    "vin": "VIN123456789",
                    "fuelKindCode": "E",
                    "dtcCount": 0,
                }
            ]
        },
    }


def test_test_token_returns_false_on_ip_validation_error():
    """7606 must invalidate the token, same as 7403/7602, so the caller
    re-authenticates instead of reusing a token the server will keep
    rejecting."""
    api = KiaUvoApiCA(region=2, brand=1, language="en")
    api._sessions = MagicMock()
    api._sessions.post.return_value = _FakeResponse(_ip_validation_error_payload())
    token = Token(
        username="u",
        password="p",
        pin="1234",
        access_token="STALE-AT",
        refresh_token="STALE-RT",
    )
    assert api.test_token(token) is False


def test_test_token_still_returns_true_when_no_error():
    api = KiaUvoApiCA(region=2, brand=1, language="en")
    api._sessions = MagicMock()
    api._sessions.post.return_value = _FakeResponse(_vehicles_ok_payload())
    token = Token(
        username="u",
        password="p",
        pin="1234",
        access_token="AT",
        refresh_token="RT",
    )
    assert api.test_token(token) is True


def test_check_and_refresh_token_reauthenticates_on_ip_validation_error():
    """Regression test for kia_uvo#1715: a fresh VehicleManager (persisted,
    unexpired token; empty in-memory vehicle cache -- the state right after
    an HA restart) must recover from a 7606 response by re-logging in and
    populating vehicles, instead of crashing with an unhandled APIError."""
    token_in = Token(
        username="user@test.com",
        password="pw",
        pin="1234",
        access_token="STALE-AT",
        refresh_token="STALE-RT",
        device_id="PERSISTED-DEVICE-ID",
        valid_until=dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=12),
    )
    vm = VehicleManager(
        region=2,
        brand=1,
        username="user@test.com",
        password="pw",
        pin="1234",
        token=token_in,
    )
    vm.api._sessions = MagicMock()
    vm.api._sessions.post.side_effect = [
        _FakeResponse(_ip_validation_error_payload()),  # test_token()
        _FakeResponse(_login_ok_payload()),  # refresh_access_token() -> login()
        _FakeResponse(
            _vehicles_ok_payload()
        ),  # initialize_vehicles() -> get_vehicles()
    ]

    with patch(
        "hyundai_kia_connect_api.KiaUvoApiCA.uuid.uuid5",
    ) as mock_uuid5:
        result = vm.check_and_refresh_token()

    assert result is True
    assert vm.token.access_token == "NEW-AT"
    assert len(vm.vehicles) == 1
    # The persisted device_id must be reused on re-login, not recomputed --
    # otherwise this would still trip the CA server's OTP/device check.
    mock_uuid5.assert_not_called()
