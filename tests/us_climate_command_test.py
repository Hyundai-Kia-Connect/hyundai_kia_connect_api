"""Tests for start_climate and stop_climate commands in HyundaiBlueLinkApiUSA.

Covers the fix for JSONDecodeError when Hyundai's API returns an empty
response body (HTTP 200 OK, no body) for climate commands.
"""

import datetime as dt
import logging
from unittest.mock import MagicMock, patch

import pytest

from hyundai_kia_connect_api.ApiImpl import ClimateRequestOptions
from hyundai_kia_connect_api.const import ENGINE_TYPES
from hyundai_kia_connect_api.HyundaiBlueLinkApiUSA import HyundaiBlueLinkApiUSA
from hyundai_kia_connect_api.Token import Token
from hyundai_kia_connect_api.Vehicle import Vehicle


class _FakeResponse:
    """Minimal fake for requests.Response."""

    def __init__(self, text="", status_code=200, headers=None):
        self.text = text
        self.status_code = status_code
        self.headers = headers or {"tmsTid": "test-transaction-id"}

    def json(self):
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
    """Create a HyundaiBlueLinkApiUSA without calling __init__."""
    api = object.__new__(HyundaiBlueLinkApiUSA)
    api.API_URL = "https://api.telematics.hyundaiusa.com/ac/v2/"
    api.session = MagicMock()
    return api


def _make_climate_options():
    return ClimateRequestOptions(set_temp=72, defrost=False, climate=True, heating=True)


class TestStartClimate:
    def test_start_climate_empty_body_no_exception(self):
        """start_climate must NOT raise JSONDecodeError on empty body."""
        api = _make_api()
        api.session.post.return_value = _FakeResponse(text="", status_code=200)
        vehicle = _make_vehicle()
        token = _make_token()
        options = _make_climate_options()

        with patch.object(api, "_get_vehicle_headers", return_value={}):
            api.start_climate(token, vehicle, options)

        api.session.post.assert_called_once()

    def test_start_climate_normal_json_response(self):
        """start_climate succeeds when API returns valid JSON."""
        api = _make_api()
        api.session.post.return_value = _FakeResponse(
            text='{"status": "success"}', status_code=200
        )
        vehicle = _make_vehicle()
        token = _make_token()
        options = _make_climate_options()

        with patch.object(api, "_get_vehicle_headers", return_value={}):
            api.start_climate(token, vehicle, options)

        api.session.post.assert_called_once()

    def test_start_climate_empty_body_logs_debug(self, caplog):
        """start_climate logs debug message when response body is empty."""
        api = _make_api()
        api.session.post.return_value = _FakeResponse(text="", status_code=200)
        vehicle = _make_vehicle()
        token = _make_token()
        options = _make_climate_options()

        with (
            caplog.at_level(logging.DEBUG),
            patch.object(api, "_get_vehicle_headers", return_value={}),
        ):
            api.start_climate(token, vehicle, options)

        assert "empty" in caplog.text.lower()


class TestStopClimate:
    def test_stop_climate_empty_body_no_exception(self):
        """stop_climate must NOT raise JSONDecodeError on empty body."""
        api = _make_api()
        api.session.post.return_value = _FakeResponse(text="", status_code=200)
        vehicle = _make_vehicle()
        token = _make_token()

        with patch.object(api, "_get_vehicle_headers", return_value={}):
            api.stop_climate(token, vehicle)

        api.session.post.assert_called_once()

    def test_stop_climate_normal_json_response(self):
        """stop_climate succeeds when API returns valid JSON."""
        api = _make_api()
        api.session.post.return_value = _FakeResponse(
            text='{"status": "success"}', status_code=200
        )
        vehicle = _make_vehicle()
        token = _make_token()

        with patch.object(api, "_get_vehicle_headers", return_value={}):
            api.stop_climate(token, vehicle)

        api.session.post.assert_called_once()

    def test_stop_climate_empty_body_logs_debug(self, caplog):
        """stop_climate logs debug message when response body is empty."""
        api = _make_api()
        api.session.post.return_value = _FakeResponse(text="", status_code=200)
        vehicle = _make_vehicle()
        token = _make_token()

        with (
            caplog.at_level(logging.DEBUG),
            patch.object(api, "_get_vehicle_headers", return_value={}),
        ):
            api.stop_climate(token, vehicle)

        assert "empty" in caplog.text.lower()


@pytest.mark.parametrize("engine_type", [ENGINE_TYPES.EV, ENGINE_TYPES.ICE])
@pytest.mark.parametrize("generation", [2, 3])
@pytest.mark.parametrize(
    ("temperature", "expected"),
    [
        (71.06, 71),
        (69.98, 70),
        (71.0, 71),
        (71, 71),
        (70.5, 71),
        (71.5, 72),
        (71.49, 71),
        (62, 62),
        (82, 82),
        # Keep the existing pass-through range policy; do not add clamping.
        (60.1, 60),
        (85.9, 86),
    ],
)
def test_start_climate_whole_fahrenheit_payload(
    engine_type, generation, temperature, expected
):
    """Normalize converted and cached targets at the Hyundai USA API boundary."""
    api = _make_api()
    api.session.post.return_value = _FakeResponse()
    vehicle = _make_vehicle()
    vehicle.engine_type = engine_type
    vehicle.generation = generation
    options = ClimateRequestOptions(set_temp=temperature)

    with patch.object(api, "_get_vehicle_headers", return_value={}):
        action_id = api.start_climate(_make_token(), vehicle, options)

    assert action_id == "test-transaction-id"
    # Normalize the wire value without changing callers' pending setpoints.
    assert options.set_temp == temperature
    assert type(options.set_temp) is type(temperature)
    api.session.post.assert_called_once()
    air_temp = api.session.post.call_args.kwargs["json"]["airTemp"]
    expected_value = str(expected) if engine_type == ENGINE_TYPES.EV else expected
    assert air_temp == {"value": expected_value, "unit": 1}
    assert type(air_temp["value"]) is type(expected_value)


@pytest.mark.parametrize("engine_type", [ENGINE_TYPES.EV, ENGINE_TYPES.ICE])
@pytest.mark.parametrize("generation", [2, 3])
@pytest.mark.parametrize("explicit_options", [False, True])
def test_start_climate_temperature_preserves_defaults_and_options(
    engine_type, generation, explicit_options
):
    """Keep duration, climate, defrost, heating and seats in each request shape."""
    api = _make_api()
    api.session.post.return_value = _FakeResponse()
    vehicle = _make_vehicle()
    vehicle.engine_type = engine_type
    vehicle.generation = generation
    token = Token(username="test-user")
    options = (
        ClimateRequestOptions(
            set_temp=71.06,
            duration=10,
            climate=False,
            defrost=True,
            heating=3,
            front_left_seat=1,
            front_right_seat=2,
            rear_left_seat=3,
            rear_right_seat=4,
        )
        if explicit_options
        else ClimateRequestOptions()
    )

    with patch.object(api, "_get_vehicle_headers", return_value={}):
        api.start_climate(token, vehicle, options)

    assert options.set_temp == (71.06 if explicit_options else 70)
    expected_temp = 71 if explicit_options else 70
    expected = {
        "airCtrl": 0 if explicit_options else 1,
        "airTemp": {
            "value": str(expected_temp)
            if engine_type == ENGINE_TYPES.EV
            else expected_temp,
            "unit": 1,
        },
        "defrost": explicit_options,
        "heating1": 3 if explicit_options else 0,
    }
    if engine_type != ENGINE_TYPES.EV or generation == 3:
        expected["igniOnDuration"] = 10 if explicit_options else 5
        expected["seatHeaterVentInfo"] = {
            "drvSeatHeatState": 1 if explicit_options else 0,
            "astSeatHeatState": 2 if explicit_options else 0,
            "rlSeatHeatState": 3 if explicit_options else 0,
            "rrSeatHeatState": 4 if explicit_options else 0,
        }
    if engine_type != ENGINE_TYPES.EV:
        expected.update(Ims=0, username=token.username, vin=vehicle.id)

    endpoint = "evc/fatc/start" if engine_type == ENGINE_TYPES.EV else "rcs/rsc/start"
    api.session.post.assert_called_once_with(
        api.API_URL + endpoint, json=expected, headers={}
    )
