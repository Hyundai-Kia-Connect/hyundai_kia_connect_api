"""GSPA control infra — maps, error classifier, control token, envelope."""

import datetime as dt
from unittest.mock import MagicMock, patch

import pytest

from hyundai_kia_connect_api.const import ORDER_STATUS
from hyundai_kia_connect_api.exceptions import (
    APIError,
    AuthenticationError,
    DuplicateRequestError,
    InvalidAPIResponseError,
    ServiceTemporaryUnavailable,
    UnsupportedControlError,
)
from hyundai_kia_connect_api.GspaApiEU import GspaApiEU
from hyundai_kia_connect_api.HyundaiCciApiEU import HyundaiCciApiEU
from hyundai_kia_connect_api.Token import Token
from hyundai_kia_connect_api.Vehicle import Vehicle


def _make_api() -> HyundaiCciApiEU:
    return HyundaiCciApiEU(9, 2, "en")


def _make_token() -> Token:
    return Token(
        username="user@test.com",
        password="MyPassword123!",
        pin="1234",
        access_token="Bearer ccs-token",
        refresh_token="REFRESHTOKEN1234567890123456789012345678901234567890",
        device_id="12345678-1234-1234-1234-123456789abc",
        valid_until=dt.datetime.now(dt.UTC) + dt.timedelta(hours=1),
        user_id="test-uid-123",
    )


def _make_vehicle(ccs2: int = 1) -> Vehicle:
    vehicle = Vehicle()
    vehicle.id = "test123"
    vehicle.ccu_ccs2_protocol_support = ccs2
    return vehicle


def test_endpoint_map():
    assert GspaApiEU.GSPA_ENDPOINT_MAP["hornlight"] == "horn-light"
    assert GspaApiEU.GSPA_ENDPOINT_MAP["windowcurtain"] == "window-curtain"


def test_path_prefix_map():
    # Valet control posts to the "control" endpoint with an explicit
    # valet path prefix in the caller (see valet_mode_action).
    assert GspaApiEU.GSPA_PATH_PREFIX_MAP["rearseat-alarm"] == "safety/vehicles"


def test_bearer_endpoints_cover_settings_and_reservations():
    assert "charge-target" in GspaApiEU.GSPA_BEARER_ENDPOINTS
    assert "lock-and-start-toggle" in GspaApiEU.GSPA_BEARER_ENDPOINTS
    assert "door" not in GspaApiEU.GSPA_BEARER_ENDPOINTS


def test_remote_vehicles_path():
    assert GspaApiEU.GSPA_REMOTE_VEHICLES_PATH == "gspa/v1/remote/vehicles"


def test_raise_gspa_error_401():
    with pytest.raises(AuthenticationError):
        _make_api()._raise_gspa_error(401, {})


def test_raise_gspa_error_duplicate():
    api = _make_api()
    for rc in ("400-004", "4004"):
        with pytest.raises(DuplicateRequestError):
            api._raise_gspa_error(200, {"rc": rc, "msg": "dup"})


def test_raise_gspa_error_403():
    with pytest.raises(AuthenticationError):
        _make_api()._raise_gspa_error(403, {"rc": "403-101", "msg": "stamp"})


def test_raise_gspa_error_404():
    with pytest.raises(UnsupportedControlError):
        _make_api()._raise_gspa_error(404, {"rc": "404-999", "msg": "nope"})


def test_raise_gspa_error_no_update_info_is_api_error():
    with pytest.raises(APIError, match="No pending OTA"):
        _make_api()._raise_gspa_error(
            404, {"rc": "404-007", "msg": "No update info found by vin"}
        )


def test_raise_gspa_error_5xx():
    api = _make_api()
    with pytest.raises(ServiceTemporaryUnavailable):
        api._raise_gspa_error(503, {})
    with pytest.raises(ServiceTemporaryUnavailable):
        api._raise_gspa_error(200, {"rc": "500-001", "msg": "boom"})


def test_raise_gspa_error_unknown_is_api_error_with_server_msg():
    with pytest.raises(APIError, match="weird business failure"):
        _make_api()._raise_gspa_error(
            400, {"rc": "400-999", "msg": "weird business failure"}
        )


PIN_RESPONSE_MATCHED = {
    "isMatched": True,
    "controlTokenInfo": {
        "controlToken": "ctrl-token-abc",
        "expiresTime": 4_000_000_000_000,  # ms epoch
    },
}

PIN_RESPONSE_NOT_MATCHED = {"isMatched": False}

CONTROL_ENVELOPE = {
    "rt": 0,
    "rc": "0000",
    "rs": {"SID": "sid-1", "svcSID": "svc"},
    "msg": "success",
}


def _post_mock(status_code: int, json_body: object) -> MagicMock:
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = json_body
    response.text = str(json_body)
    return response


def test_control_token_no_pin_raises():
    token = _make_token()
    token.pin = None
    with pytest.raises(UnsupportedControlError):
        _make_api()._get_control_token(token)


def test_control_token_cci_success():
    token = _make_token()
    with patch("hyundai_kia_connect_api.GspaApiEU.requests.post") as post:
        post.return_value = _post_mock(200, PIN_RESPONSE_MATCHED)
        control_token, expire_at = _make_api()._get_control_token(token)
    assert control_token == "Bearer ctrl-token-abc"
    assert expire_at == 4_000_000_000  # ms -> s
    # Cache lives on the Token (Type1 pattern), not on the API instance.
    assert token.control_token == "Bearer ctrl-token-abc"
    assert token.control_token_expiry == 4_000_000_000
    assert post.call_args.args[0].endswith("/domain/api/v1/auth/pin")
    assert post.call_args.kwargs["json"] == {"pin": "1234"}


def test_control_token_ttl_seconds_live_shape():
    """Live-probed shape (2026-09-04): expiresTime=600 is a TTL in seconds
    (10 min), not an epoch — expire_at must be now + 600."""
    with patch("hyundai_kia_connect_api.GspaApiEU.requests.post") as post:
        post.return_value = _post_mock(
            200,
            {
                "isMatched": True,
                "controlTokenInfo": {"controlToken": "t", "expiresTime": 600},
            },
        )
        _, expire_at = _make_api()._get_control_token(_make_token())
    assert expire_at == pytest.approx(dt.datetime.now(dt.UTC).timestamp() + 600, abs=10)


def test_control_token_pin_mismatch_raises():
    with patch("hyundai_kia_connect_api.GspaApiEU.requests.post") as post:
        post.return_value = _post_mock(200, PIN_RESPONSE_NOT_MATCHED)
        with pytest.raises(APIError, match="PIN verification failed"):
            _make_api()._get_control_token(_make_token())


def test_control_token_missing_control_token_raises():
    body = {"isMatched": True, "controlTokenInfo": {"expiresTime": 1}}
    with patch("hyundai_kia_connect_api.GspaApiEU.requests.post") as post:
        post.return_value = _post_mock(200, body)
        with pytest.raises(InvalidAPIResponseError):
            _make_api()._get_control_token(_make_token())


def test_control_token_cache_lives_on_token():
    """The control token is cached on the Token object (Type1 pattern):
    a second call with the same Token reuses it without a new PIN POST,
    and invalidation clears the Token fields."""
    api = _make_api()
    token = _make_token()
    with patch("hyundai_kia_connect_api.GspaApiEU.requests.post") as post:
        post.return_value = _post_mock(200, PIN_RESPONSE_MATCHED)
        first = api._get_control_token(token)
        second = api._get_control_token(token)
        assert first == second
        assert first[0] == "Bearer ctrl-token-abc"
        assert post.call_count == 1  # one PIN POST for two commands
    api._invalidate_control_token(token)
    assert token.control_token is None
    assert token.control_token_expiry == 0.0
    with patch("hyundai_kia_connect_api.GspaApiEU.requests.post") as post:
        post.return_value = _post_mock(200, PIN_RESPONSE_MATCHED)
        api._get_control_token(token)
        assert post.call_count == 1  # refetched after invalidation


def test_control_token_cache_is_structurally_per_token():
    """A fresh Token (e.g. after a re-login) has no control token, so the
    PIN is verified again — the one-to-one mapping is structural, with no
    source-token bookkeeping."""
    api = _make_api()
    with patch("hyundai_kia_connect_api.GspaApiEU.requests.post") as post:
        post.return_value = _post_mock(200, PIN_RESPONSE_MATCHED)
        old_token = _make_token()
        api._get_control_token(old_token)
        assert post.call_count == 1
        fresh_token = _make_token()
        fresh_token.access_token = "Bearer fresh-ccs-token"
        api._get_control_token(fresh_token)
        assert post.call_count == 2  # fresh Token -> PIN verified again
        assert fresh_token.control_token == "Bearer ctrl-token-abc"


def test_control_token_persists_through_token_serialization():
    """control_token/control_token_expiry ride Token.to_dict/from_dict, so
    a persisted Token (kia_uvo) keeps a still-valid control token across
    restarts."""
    api = _make_api()
    token = _make_token()
    with patch("hyundai_kia_connect_api.GspaApiEU.requests.post") as post:
        post.return_value = _post_mock(200, PIN_RESPONSE_MATCHED)
        api._get_control_token(token)
    restored = Token.from_dict(token.to_dict())
    assert restored.control_token == "Bearer ctrl-token-abc"
    assert restored.control_token_expiry == token.control_token_expiry
    with patch("hyundai_kia_connect_api.GspaApiEU.requests.post") as post:
        api._get_control_token(restored)  # cache hit — no PIN POST
        assert post.call_count == 0


def test_control_headers_carry_authorization_ccsp():
    token = _make_token()
    token.control_token = "Bearer ctrl-token-abc"
    token.control_token_expiry = dt.datetime.now(dt.UTC).timestamp() + 3600
    headers = _make_api()._get_control_headers(token, _make_vehicle())
    assert headers["Authorization"] == "Bearer ctrl-token-abc"
    assert headers["AuthorizationCCSP"] == "Bearer ctrl-token-abc"
    assert "X-Stamp" in headers


def test_control_request_headers_bearer_endpoint_has_no_ccsp():
    api = _make_api()
    headers = api._get_control_request_headers(
        _make_token(), _make_vehicle(), "charge-target"
    )
    assert headers["Authorization"] == "Bearer ccs-token"
    assert "AuthorizationCCSP" not in headers


def test_gspa_control_command_url_body_and_sid():
    api = _make_api()
    with (
        patch("hyundai_kia_connect_api.GspaApiEU.requests.post") as post,
        patch.object(HyundaiCciApiEU, "_get_control_token") as get_ct,
    ):
        get_ct.return_value = ("Bearer ctrl-token-abc", 4_000_000_000)
        post.return_value = _post_mock(200, CONTROL_ENVELOPE)
        action_id = api._gspa_control_command(
            _make_token(), _make_vehicle(), "door", {"command": "close"}
        )
    assert action_id == "gspa:sid-1"
    url = post.call_args.args[0]
    assert url == "https://gspa-ccs-eu.hyundai.com/gspa/v1/remote/vehicles/test123/door"
    assert post.call_args.kwargs["json"] == {"command": "close"}
    headers = post.call_args.kwargs["headers"]
    assert headers["AuthorizationCCSP"] == "Bearer ctrl-token-abc"


def test_gspa_control_command_strips_legacy_action_and_device_id():
    api = _make_api()
    with (
        patch("hyundai_kia_connect_api.GspaApiEU.requests.post") as post,
        patch.object(HyundaiCciApiEU, "_get_control_token") as get_ct,
    ):
        get_ct.return_value = ("Bearer ctrl-token-abc", 4_000_000_000)
        post.return_value = _post_mock(200, CONTROL_ENVELOPE)
        api._gspa_control_command(
            _make_token(),
            _make_vehicle(),
            "door",
            {"action": "close", "deviceId": "legacy-device"},
        )
    assert post.call_args.kwargs["json"] == {"command": "close"}


def test_gspa_control_command_sid_fallback_to_rs():
    api = _make_api()
    envelope = {"rc": "0000", "rs": {"SID": "sid-from-rs"}}
    with (
        patch("hyundai_kia_connect_api.GspaApiEU.requests.post") as post,
        patch.object(HyundaiCciApiEU, "_get_control_token") as get_ct,
    ):
        get_ct.return_value = ("Bearer ctrl-token-abc", 4_000_000_000)
        post.return_value = _post_mock(200, envelope)
        assert (
            api._gspa_control_command(
                _make_token(), _make_vehicle(), "charge", {"command": "start"}
            )
            == "gspa:sid-from-rs"
        )


def test_gspa_control_command_missing_sid_returns_bare_prefix():
    """Live-probed 2026-09-05: a command can be accepted (retCode S)
    with no SID at all — the bare "gspa:" prefix is returned instead of
    raising, because the server has accepted the command."""
    api = _make_api()
    with (
        patch("hyundai_kia_connect_api.GspaApiEU.requests.post") as post,
        patch.object(HyundaiCciApiEU, "_get_control_token") as get_ct,
    ):
        get_ct.return_value = ("Bearer ctrl-token-abc", 4_000_000_000)
        post.return_value = _post_mock(200, {"rc": "0000", "rs": {}})
        assert (
            api._gspa_control_command(
                _make_token(), _make_vehicle(), "charge", {"command": "start"}
            )
            == "gspa:"
        )


def test_gspa_control_command_rc_error_raises_typed():
    api = _make_api()
    with (
        patch("hyundai_kia_connect_api.GspaApiEU.requests.post") as post,
        patch.object(HyundaiCciApiEU, "_get_control_token") as get_ct,
    ):
        get_ct.return_value = ("Bearer ctrl-token-abc", 4_000_000_000)
        post.return_value = _post_mock(400, {"rc": "400-999", "msg": "bad request"})
        with pytest.raises(APIError, match="bad request"):
            api._gspa_control_command(
                _make_token(), _make_vehicle(), "door", {"command": "close"}
            )


def test_gspa_control_command_401_invalidates_cache_and_retries_once():
    """A 401 on a PIN-gated command clears the Token's control token and
    retries exactly once with a freshly verified PIN (no method mocks —
    the real _get_control_token cache path is exercised)."""
    api = _make_api()
    token = _make_token()
    token.control_token = "Bearer stale"
    token.control_token_expiry = dt.datetime.now(dt.UTC).timestamp() + 3600
    with patch("hyundai_kia_connect_api.GspaApiEU.requests.post") as post:
        post.side_effect = [
            _post_mock(401, {}),  # control command: stale token rejected
            _post_mock(200, PIN_RESPONSE_MATCHED),  # PIN re-verified
            _post_mock(200, CONTROL_ENVELOPE),  # retried command
        ]
        action_id = api._gspa_control_command(
            token, _make_vehicle(), "door", {"command": "close"}
        )
    assert action_id == "gspa:sid-1"
    assert post.call_count == 3  # 401 + PIN re-verify + retried command
    assert token.control_token == "Bearer ctrl-token-abc"
    assert post.call_args_list[1].args[0].endswith("/domain/api/v1/auth/pin")


def test_gspa_control_command_path_prefix_override():
    api = _make_api()
    with (
        patch("hyundai_kia_connect_api.GspaApiEU.requests.post") as post,
        patch.object(HyundaiCciApiEU, "_get_control_token") as get_ct,
    ):
        get_ct.return_value = ("Bearer ctrl-token-abc", 4_000_000_000)
        post.return_value = _post_mock(200, CONTROL_ENVELOPE)
        api._gspa_control_command(
            _make_token(),
            _make_vehicle(),
            "control",
            {"command": "activate"},
            path_prefix="valet/vehicles",
        )
    assert post.call_args.args[0].endswith("/gspa/v1/valet/vehicles/test123/control")


def test_get_control_token_pin_failure_reports_remaining_attempts():
    """Live: wrong PIN returns HTTP 200 with remainCountOnFailedInfo."""
    api = _make_api()
    body = {
        "isMatched": False,
        "controlTokenInfo": None,
        "remainCountOnFailedInfo": {
            "remainCount": 4,
            "remainTime": 300,
            "timeUnit": "SECONDS",
        },
    }
    with patch("hyundai_kia_connect_api.GspaApiEU.requests.post") as post:
        post.return_value = _post_mock(200, body)
        with pytest.raises(APIError, match=r"4 attempts remaining"):
            api._get_control_token(_make_token())


def test_get_control_token_pin_locked_reports_window():
    """Live: after 5 failures (remainCount 0) even the correct pin is
    rejected with the same shape until the window passes."""
    api = _make_api()
    body = {
        "isMatched": False,
        "controlTokenInfo": None,
        "remainCountOnFailedInfo": {
            "remainCount": 0,
            "remainTime": 300,
            "timeUnit": "SECONDS",
        },
    }
    with patch("hyundai_kia_connect_api.GspaApiEU.requests.post") as post:
        post.return_value = _post_mock(200, body)
        with pytest.raises(APIError, match="temporarily locked.*300s"):
            api._get_control_token(_make_token())


def _status_response(polling_state: str) -> dict:
    return {
        "metaInfo": {"retCode": "S", "resCode": "200-000"},
        "data": {"pollingState": polling_state},
    }


def test_check_action_status_success():
    api = _make_api()
    with patch("hyundai_kia_connect_api.GspaApiEU.requests.get") as get:
        get.return_value = _post_mock(200, _status_response("SUCCESS"))
        status = api._gspa_check_action_status(_make_token(), _make_vehicle(), "sid-1")
    assert status is ORDER_STATUS.SUCCESS
    assert "/gspa/v1/status/vehicles/test123/update-status" in get.call_args.args[0]
    assert "path=gspa/v1/remote/vehicles" in get.call_args.args[0]


def test_check_action_status_wait_failure_timeout():
    from hyundai_kia_connect_api.const import ORDER_STATUS

    api = _make_api()
    expected = {
        "WAIT": ORDER_STATUS.PENDING,
        "FAILURE": ORDER_STATUS.FAILED,
        "TIMEOUT": ORDER_STATUS.TIMEOUT,
    }
    for state, want in expected.items():
        with patch("hyundai_kia_connect_api.GspaApiEU.requests.get") as get:
            get.return_value = _post_mock(200, _status_response(state))
            assert (
                api._gspa_check_action_status(_make_token(), _make_vehicle(), "sid-1")
                is want
            )


def test_check_action_status_transport_error_is_pending():
    from hyundai_kia_connect_api.const import ORDER_STATUS

    api = _make_api()
    with patch("hyundai_kia_connect_api.GspaApiEU.requests.get") as get:
        get.return_value = _post_mock(500, {})
        assert (
            api._gspa_check_action_status(_make_token(), _make_vehicle(), "sid-1")
            is ORDER_STATUS.PENDING
        )
    with patch("hyundai_kia_connect_api.GspaApiEU.requests.get") as get:
        get.return_value = _post_mock(200, {"metaInfo": {"retCode": "F"}})
        assert (
            api._gspa_check_action_status(_make_token(), _make_vehicle(), "sid-1")
            is ORDER_STATUS.PENDING
        )


def test_check_action_status_202_accepted():
    """Live: a successful poll returns HTTP 202 (202-000 Accepted)."""
    api = _make_api()
    with patch("hyundai_kia_connect_api.GspaApiEU.requests.get") as get:
        get.return_value = _post_mock(202, _status_response("SUCCESS"))
        status = api._gspa_check_action_status(_make_token(), _make_vehicle(), "sid-1")
    assert status is ORDER_STATUS.SUCCESS
