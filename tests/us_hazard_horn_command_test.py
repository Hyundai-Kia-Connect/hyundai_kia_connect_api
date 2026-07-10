"""Tests for start_hazard_lights and start_hazard_lights_and_horn in HyundaiBlueLinkApiUSA."""

import datetime as dt
from unittest.mock import MagicMock, patch

from hyundai_kia_connect_api.HyundaiBlueLinkApiUSA import HyundaiBlueLinkApiUSA
from hyundai_kia_connect_api.Token import Token
from hyundai_kia_connect_api.Vehicle import Vehicle
from hyundai_kia_connect_api.const import ORDER_STATUS


class _FakeResponse:
    """Minimal fake for requests.Response."""

    def __init__(self, text="", json_data=None, status_code=200, headers=None):
        self.json_data = json_data
        self.status_code = status_code
        self.headers = headers or {"tmsTid": "test-transaction-id"}
        self.text = text if text else ('{"status": "success"}' if json_data else "")

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
    token = MagicMock(spec=Token)
    token.username = "test-user"
    return token


def _make_api():
    """Create a HyundaiBlueLinkApiUSA without calling __init__."""
    api = object.__new__(HyundaiBlueLinkApiUSA)
    api.API_URL = "https://api.telematics.hyundaiusa.com/ac/v2/"
    api.session = MagicMock()
    api._action_service_types = {}
    return api


class TestStartHazardLights:
    def test_empty_body_no_exception(self):
        """start_hazard_lights must NOT raise JSONDecodeError on empty body."""
        api = _make_api()
        api.session.post.return_value = _FakeResponse(text="", status_code=200)
        vehicle = _make_vehicle()
        token = _make_token()

        with patch.object(api, "_get_vehicle_headers", return_value={}):
            api.start_hazard_lights(token, vehicle)

        api.session.post.assert_called_once()

    def test_normal_json_response(self):
        """start_hazard_lights succeeds when API returns valid JSON."""
        api = _make_api()
        api.session.post.return_value = _FakeResponse(
            text='{"status": "success"}', status_code=200
        )
        vehicle = _make_vehicle()
        token = _make_token()

        with patch.object(api, "_get_vehicle_headers", return_value={}):
            api.start_hazard_lights(token, vehicle)

        api.session.post.assert_called_once()

    def test_calls_correct_url(self):
        """start_hazard_lights POSTs to rcs/rhl/light."""
        api = _make_api()
        api.session.post.return_value = _FakeResponse(text="", status_code=200)
        vehicle = _make_vehicle()
        token = _make_token()

        with patch.object(api, "_get_vehicle_headers", return_value={}):
            api.start_hazard_lights(token, vehicle)

        call_url = api.session.post.call_args[0][0]
        assert "rcs/rhl/light" in call_url

    def test_sends_username_and_vin(self):
        """start_hazard_lights sends userName and vin in JSON body."""
        api = _make_api()
        api.session.post.return_value = _FakeResponse(text="", status_code=200)
        vehicle = _make_vehicle()
        token = _make_token()

        with patch.object(api, "_get_vehicle_headers", return_value={}):
            api.start_hazard_lights(token, vehicle)

        call_kwargs = api.session.post.call_args[1]
        assert call_kwargs["json"] == {"userName": "test-user", "vin": vehicle.VIN}

    def test_appcloud_vin_header(self):
        """start_hazard_lights sets APPCLOUD-VIN header."""
        api = _make_api()
        api.session.post.return_value = _FakeResponse(text="", status_code=200)
        vehicle = _make_vehicle()
        token = _make_token()
        base_headers = {"registrationId": "test-id"}

        with patch.object(api, "_get_vehicle_headers", return_value=base_headers):
            api.start_hazard_lights(token, vehicle)

        call_kwargs = api.session.post.call_args[1]
        sent_headers = call_kwargs["headers"]
        assert sent_headers["APPCLOUD-VIN"] == vehicle.VIN
        assert sent_headers["registrationId"] == "test-id"

    def test_stores_lights_only_service_type(self):
        """start_hazard_lights registers LIGHTS_ONLY for action status polling."""
        api = _make_api()
        api.session.post.return_value = _FakeResponse(text="", status_code=200)
        vehicle = _make_vehicle()
        token = _make_token()

        with patch.object(api, "_get_vehicle_headers", return_value={}):
            action_id = api.start_hazard_lights(token, vehicle)

        assert action_id == "test-transaction-id"
        assert api._action_service_types[action_id] == "LIGHTS_ONLY"


class TestStartHazardLightsAndHorn:
    def test_empty_body_no_exception(self):
        """start_hazard_lights_and_horn must NOT raise JSONDecodeError on empty body."""
        api = _make_api()
        api.session.post.return_value = _FakeResponse(text="", status_code=200)
        vehicle = _make_vehicle()
        token = _make_token()

        with patch.object(api, "_get_vehicle_headers", return_value={}):
            api.start_hazard_lights_and_horn(token, vehicle)

        api.session.post.assert_called_once()

    def test_normal_json_response(self):
        """start_hazard_lights_and_horn succeeds when API returns valid JSON."""
        api = _make_api()
        api.session.post.return_value = _FakeResponse(
            text='{"status": "success"}', status_code=200
        )
        vehicle = _make_vehicle()
        token = _make_token()

        with patch.object(api, "_get_vehicle_headers", return_value={}):
            api.start_hazard_lights_and_horn(token, vehicle)

        api.session.post.assert_called_once()

    def test_calls_correct_url(self):
        """start_hazard_lights_and_horn POSTs to rcs/rhl/hnl."""
        api = _make_api()
        api.session.post.return_value = _FakeResponse(text="", status_code=200)
        vehicle = _make_vehicle()
        token = _make_token()

        with patch.object(api, "_get_vehicle_headers", return_value={}):
            api.start_hazard_lights_and_horn(token, vehicle)

        call_url = api.session.post.call_args[0][0]
        assert "rcs/rhl/hnl" in call_url

    def test_sends_username_and_vin(self):
        """start_hazard_lights_and_horn sends userName and vin in JSON body."""
        api = _make_api()
        api.session.post.return_value = _FakeResponse(text="", status_code=200)
        vehicle = _make_vehicle()
        token = _make_token()

        with patch.object(api, "_get_vehicle_headers", return_value={}):
            api.start_hazard_lights_and_horn(token, vehicle)

        call_kwargs = api.session.post.call_args[1]
        assert call_kwargs["json"] == {"userName": "test-user", "vin": vehicle.VIN}

    def test_stores_horn_and_lights_service_type(self):
        """start_hazard_lights_and_horn registers HORN_AND_LIGHTS for action status polling."""
        api = _make_api()
        api.session.post.return_value = _FakeResponse(text="", status_code=200)
        vehicle = _make_vehicle()
        token = _make_token()

        with patch.object(api, "_get_vehicle_headers", return_value={}):
            action_id = api.start_hazard_lights_and_horn(token, vehicle)

        assert action_id == "test-transaction-id"
        assert api._action_service_types[action_id] == "HORN_AND_LIGHTS"


class TestActionStatusServiceTypeMapping:
    def test_hazard_lights_uses_lights_only_in_status_poll(self):
        """check_action_status uses LIGHTS_ONLY after start_hazard_lights."""
        api = _make_api()
        api.session.post.return_value = _FakeResponse(text="", status_code=200)
        api.session.get.return_value = _FakeResponse(
            json_data={"status": "SUCCESS"}, status_code=200
        )
        vehicle = _make_vehicle()
        token = _make_token()

        with patch.object(api, "_get_vehicle_headers", return_value={}):
            action_id = api.start_hazard_lights(token, vehicle)
            result = api.check_action_status(token, vehicle, action_id)

        assert result == ORDER_STATUS.SUCCESS
        call_kwargs = api.session.get.call_args[1]
        assert call_kwargs["headers"]["service_type"] == "LIGHTS_ONLY"

    def test_hazard_lights_and_horn_uses_horn_and_lights_in_status_poll(self):
        """check_action_status uses HORN_AND_LIGHTS after start_hazard_lights_and_horn."""
        api = _make_api()
        api.session.post.return_value = _FakeResponse(text="", status_code=200)
        api.session.get.return_value = _FakeResponse(
            json_data={"status": "SUCCESS"}, status_code=200
        )
        vehicle = _make_vehicle()
        token = _make_token()

        with patch.object(api, "_get_vehicle_headers", return_value={}):
            action_id = api.start_hazard_lights_and_horn(token, vehicle)
            result = api.check_action_status(token, vehicle, action_id)

        assert result == ORDER_STATUS.SUCCESS
        call_kwargs = api.session.get.call_args[1]
        assert call_kwargs["headers"]["service_type"] == "HORN_AND_LIGHTS"

    def test_unknown_action_uses_remote_poll_default(self):
        """check_action_status uses REMOTE_POLL for action IDs not from horn/hazard."""
        api = _make_api()
        api.session.get.return_value = _FakeResponse(
            json_data={"status": "SUCCESS"}, status_code=200
        )
        vehicle = _make_vehicle()
        token = _make_token()

        with patch.object(api, "_get_vehicle_headers", return_value={}):
            result = api.check_action_status(token, vehicle, "unknown-tx")

        assert result == ORDER_STATUS.SUCCESS
        call_kwargs = api.session.get.call_args[1]
        assert call_kwargs["headers"]["service_type"] == "REMOTE_POLL"
