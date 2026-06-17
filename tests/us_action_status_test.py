"""Tests for check_action_status in HyundaiBlueLinkApiUSA."""

import datetime as dt
from unittest.mock import MagicMock, patch

from hyundai_kia_connect_api.HyundaiBlueLinkApiUSA import HyundaiBlueLinkApiUSA
from hyundai_kia_connect_api.Token import Token
from hyundai_kia_connect_api.Vehicle import Vehicle
from hyundai_kia_connect_api.const import ORDER_STATUS


class _FakeResponse:
    """Minimal fake for requests.Response."""

    def __init__(self, json_data=None, status_code=200):
        self.json_data = json_data
        self.status_code = status_code
        self.text = '{"status": "success"}' if json_data else ""
        self.headers = {"tmsTid": "test-transaction-id"}

    def json(self):
        if self.json_data is not None:
            return self.json_data
        import json

        return json.loads(self.text)


def _make_vehicle():
    return Vehicle(
        id="test-id",
        name="Ioniq 5",
        model="IONIQ 5",
        key="test-key",
        timezone=dt.timezone(dt.timedelta(hours=-5)),
    )


def _make_token():
    return MagicMock(spec=Token)


def _make_api():
    api = object.__new__(HyundaiBlueLinkApiUSA)
    api.API_URL = "https://api.telematics.hyundaiusa.com/ac/v2/"
    api.sessions = MagicMock()
    return api


class TestCheckActionStatus:
    def test_success(self):
        api = _make_api()
        api.sessions.get.return_value = _FakeResponse(json_data={"status": "SUCCESS"})
        vehicle = _make_vehicle()
        token = _make_token()

        with patch.object(api, "_get_vehicle_headers", return_value={}):
            result = api.check_action_status(token, vehicle, "tx-123")

        assert result == ORDER_STATUS.SUCCESS

    def test_failed(self):
        api = _make_api()
        api.sessions.get.return_value = _FakeResponse(json_data={"status": "ERROR"})
        vehicle = _make_vehicle()
        token = _make_token()

        with patch.object(api, "_get_vehicle_headers", return_value={}):
            result = api.check_action_status(token, vehicle, "tx-123")

        assert result == ORDER_STATUS.FAILED

    def test_pending(self):
        api = _make_api()
        api.sessions.get.return_value = _FakeResponse(json_data={"status": "PENDING"})
        vehicle = _make_vehicle()
        token = _make_token()

        with patch.object(api, "_get_vehicle_headers", return_value={}):
            result = api.check_action_status(token, vehicle, "tx-123")

        assert result == ORDER_STATUS.PENDING

    def test_empty_response_returns_unknown(self):
        api = _make_api()
        api.sessions.get.return_value = _FakeResponse(json_data=None)
        vehicle = _make_vehicle()
        token = _make_token()

        with patch.object(api, "_get_vehicle_headers", return_value={}):
            result = api.check_action_status(token, vehicle, "tx-123")

        assert result == ORDER_STATUS.UNKNOWN

    def test_synchronous_timeout(self):
        api = _make_api()
        api.sessions.get.return_value = _FakeResponse(json_data={"status": "PENDING"})
        vehicle = _make_vehicle()
        token = _make_token()

        with patch.object(api, "_get_vehicle_headers", return_value={}):
            with patch("hyundai_kia_connect_api.HyundaiBlueLinkApiUSA.time.sleep"):
                result = api.check_action_status(
                    token, vehicle, "tx-123", synchronous=True, timeout=4
                )

        assert result == ORDER_STATUS.TIMEOUT

    def test_synchronous_success(self):
        api = _make_api()
        api.sessions.get.return_value = _FakeResponse(json_data={"status": "SUCCESS"})
        vehicle = _make_vehicle()
        token = _make_token()

        with patch.object(api, "_get_vehicle_headers", return_value={}):
            result = api.check_action_status(
                token, vehicle, "tx-123", synchronous=True, timeout=4
            )

        assert result == ORDER_STATUS.SUCCESS


class TestGetTransactionId:
    def test_finds_tmsTid(self):
        api = _make_api()
        response = MagicMock()
        response.headers = {"tmsTid": "abc-123"}
        assert api._get_transaction_id(response) == "abc-123"

    def test_finds_transactionId(self):
        api = _make_api()
        response = MagicMock()
        response.headers = {"transactionId": "def-456"}
        assert api._get_transaction_id(response) == "def-456"

    def test_finds_Xid(self):
        api = _make_api()
        response = MagicMock()
        response.headers = {"Xid": "ghi-789"}
        assert api._get_transaction_id(response) == "ghi-789"

    def test_returns_none_when_not_found(self):
        api = _make_api()
        response = MagicMock()
        response.headers = {"content-type": "application/json"}
        assert api._get_transaction_id(response) is None
