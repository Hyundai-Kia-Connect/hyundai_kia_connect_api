"""Tests for KiaUvoApiCA device_id persistence (kia_uvo#1715).

CA has no refresh endpoint, so token refresh re-logs-in. Previously device_id
was regenerated from uuid5(MAC+hostname) on every login and never stored in
the Token — a Docker restart with a changed MAC/hostname produced a new
device_id the server did not recognize, triggering 7110 OTP. These tests lock
in the fix: device_id is cached on the instance, seeded from the persisted
Token on refresh, and written into the returned Token so the HA coordinator
persists it.
"""

import base64
import uuid
from unittest.mock import MagicMock, patch

from hyundai_kia_connect_api.ApiImpl import OTPRequest
from hyundai_kia_connect_api.KiaUvoApiCA import KiaUvoApiCA


class _FakeResponse:
    """Minimal stand-in for requests.Response used by CA login/OTP flows."""

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload

    @property
    def text(self):
        import json

        return json.dumps(self._payload)


def _login_ok_payload():
    return {
        "responseHeader": {"responseCode": 0},
        "result": {
            "token": {
                "expireIn": 86400,
                "accessToken": "AT",
                "refreshToken": "RT",
            }
        },
    }


def test_get_device_id_caches_first_computation():
    api = KiaUvoApiCA(region=2, brand=1, language="en")
    sentinel = uuid.UUID("11111111-2222-3333-4444-555566667777")
    with patch(
        "hyundai_kia_connect_api.KiaUvoApiCA.uuid.uuid5", return_value=sentinel
    ) as mock_uuid5:
        first = api._get_device_id()
        second = api._get_device_id()
    assert first == second
    assert mock_uuid5.call_count == 1
    assert first == base64.b64encode(sentinel.hex.encode()).decode()


def test_login_persists_device_id_in_token():
    api = KiaUvoApiCA(region=2, brand=1, language="en")
    api._sessions = MagicMock()
    api._sessions.post.return_value = _FakeResponse(_login_ok_payload())
    token = api.login(username="user@test.com", password="pw", pin="1234")
    assert token.device_id is not None
    assert token.device_id == api._device_id


def test_verify_otp_and_complete_login_persists_device_id_in_token():
    api = KiaUvoApiCA(region=2, brand=1, language="en")
    validate_resp = {
        "responseHeader": {"responseCode": 0},
        "result": {"verifiedOtp": True, "otpValidationKey": "OVK"},
    }
    genmfatkn_resp = {
        "responseHeader": {"responseCode": 0},
        "result": {
            "token": {
                "expireIn": 86400,
                "accessToken": "AT",
                "refreshToken": "RT",
            }
        },
    }
    api._sessions = MagicMock()
    api._sessions.post.side_effect = [
        _FakeResponse(validate_resp),
        _FakeResponse(genmfatkn_resp),
    ]
    otp_request = OTPRequest(
        request_id="user-info-uuid",
        otp_key="OTPKEY",
        has_email=True,
        has_sms=False,
        email="user@test.com",
        sms=None,
    )
    token = api.verify_otp_and_complete_login(
        username="user@test.com",
        password="pw",
        otp_code="9999",
        otp_request=otp_request,
        pin="1234",
    )
    assert token.device_id is not None
    assert token.device_id == api._device_id
