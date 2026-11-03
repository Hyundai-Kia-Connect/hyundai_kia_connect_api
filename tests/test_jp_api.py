"""Synthetic Japan contracts; every request is mocked and no account is used."""

import datetime as dt
import logging
from unittest.mock import Mock, PropertyMock

import pytest
import requests

from hyundai_kia_connect_api.const import ORDER_STATUS
from hyundai_kia_connect_api.exceptions import (
    APIError,
    AuthenticationError,
    ConsentRequiredError,
    DuplicateRequestError,
    InvalidAPIResponseError,
    NoDataFound,
    RateLimitingError,
    RequestTimeoutError,
    ServiceTemporaryUnavailable,
)
from hyundai_kia_connect_api.HyundaiBlueLinkApiJP import HyundaiBlueLinkApiJP
from hyundai_kia_connect_api.Token import Token
from hyundai_kia_connect_api.Vehicle import Vehicle
from hyundai_kia_connect_api.VehicleManager import VehicleManager


def envelope(payload):
    return {"retCode": "S", "resCode": "0000", "resMsg": payload}


def response(payload, status=200):
    result = Mock(status_code=status)
    result.json.return_value = payload
    return result


@pytest.fixture
def api():
    result = HyundaiBlueLinkApiJP(10, 2, "en")
    result.session = Mock()
    return result


def test_factory_and_capabilities():
    api = VehicleManager.get_implementation_by_region_brand(10, 2, "en")
    assert isinstance(api, HyundaiBlueLinkApiJP)
    assert api.supports_force_refresh is True
    assert api.requires_command_confirmation is True
    assert "schedule_charging_and_climate" in api.supported_commands
    for brand in (1, 3):
        with pytest.raises(APIError):
            VehicleManager.get_implementation_by_region_brand(10, brand, "en")


@pytest.mark.parametrize(
    "epoch,expected",
    [
        (
            1791846000,
            "8AXi68sDH6x6xivBKGT84+YiFhG4wOHVJVpLyjVNr5WMJKGtK8ZnraiLMUhD1qk=",
        ),
        (
            1999999999,
            "8AXi68sDH6x6xivBKGT84+YiFhG4wOHVJVpLyjVNr5WMJKGtK8ZpraCKPEdK36A=",
        ),
        (
            2000000000,
            "8AXi68sDH6x6xivBKGT84+YiFhG4wOHVJVpLyjVNr5WMJKGtK8VgpKmDNU5D1qk=",
        ),
    ],
)
def test_stamp_offline_native_golden_vectors(api, monkeypatch, epoch, expected):
    monkeypatch.setattr("time.time", lambda: epoch)
    assert api._stamp() == expected


def test_stamp_rejects_unsupported_epoch(api, monkeypatch):
    monkeypatch.setattr("time.time", lambda: 10000000000)
    with pytest.raises(APIError):
        api._stamp()


def test_login_contract(api):
    api.session.request.side_effect = [
        response(None),
        response({}),
        response({"redirectUrl": api.REDIRECT_URI + "?code=SYNTHETIC_CODE"}),
        response(
            {
                "access_token": "SYNTHETIC_ACCESS",
                "token_type": "Bearer",
                "refresh_token": "SYNTHETIC_REFRESH",
                "expires_in": 3600,
            }
        ),
        response(envelope({"deviceId": "SYNTHETIC_DEVICE"})),
    ]
    before = dt.datetime.now(dt.UTC)
    token = api.login("SYNTHETIC_USER", "SYNTHETIC_PASSWORD", "1234")
    assert token.device_id == "SYNTHETIC_DEVICE"
    assert token.access_token == "Bearer SYNTHETIC_ACCESS"
    assert before + dt.timedelta(seconds=3600) <= token.valid_until
    calls = api.session.request.call_args_list
    assert [call.args[0] for call in calls] == ["GET", "POST", "POST", "POST", "POST"]
    assert calls[0].kwargs["params"] == {
        "response_type": "code",
        "client_id": api.SERVICE_ID,
        "redirect_uri": api.REDIRECT_URI,
        "lang": "ja",
    }
    assert calls[1].kwargs["json"] == {"lang": "ja"}
    assert calls[3].kwargs["data"]["code"] == "SYNTHETIC_CODE"
    assert calls[4].kwargs["headers"]["Authorization"] == ""
    assert "Stamp" in calls[4].kwargs["headers"]


@pytest.mark.parametrize(
    "signin,error",
    [
        ({"step": "consent"}, ConsentRequiredError),
        ({"redirectUrl": "https://example.invalid/?code=secret"}, AuthenticationError),
        ({"redirectUrl": "bad"}, AuthenticationError),
    ],
)
def test_login_nonterminal_or_untrusted_redirect(api, signin, error):
    api.session.request.side_effect = [response(None), response({}), response(signin)]
    with pytest.raises(error):
        api.login("synthetic", "synthetic")
    assert api.session.request.call_count == 3


def test_refresh_preserves_device_and_refresh_token(api):
    token = Token(
        username="user",
        password="pass",
        device_id="device",
        refresh_token="refresh",
        pin="1234",
    )
    api.session.request.return_value = response(
        {"access_token": "access", "token_type": "Bearer", "expires_in": 600}
    )
    renewed = api.refresh_access_token(token)
    assert renewed.device_id == "device"
    assert renewed.refresh_token == "refresh"
    assert renewed.pin == "1234"
    assert api.session.request.call_args.kwargs["data"]["grant_type"] == "refresh_token"


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"access_token": "x", "token_type": "Bearer", "expires_in": 0},
        {"access_token": "x", "token_type": "Bearer", "expires_in": "bad"},
        {"access_token": None, "token_type": "Bearer", "expires_in": 60},
        {"access_token": "x", "token_type": None, "expires_in": 60},
        {"access_token": "x", "token_type": "Bearer", "expires_in": True},
        {"access_token": "x", "token_type": "Bearer", "expires_in": "9" * 100},
        {
            "access_token": "x",
            "token_type": "Bearer",
            "expires_in": 60,
            "refresh_token": [],
        },
    ],
)
def test_invalid_token_schema(api, payload):
    api.session.request.return_value = response(payload)
    with pytest.raises(InvalidAPIResponseError):
        api.refresh_access_token(Token(refresh_token="synthetic"))


def test_discovery_requires_profile_protocol_and_uses_tokyo(api):
    api.session.request.side_effect = [
        response(
            envelope(
                {
                    "vehicles": [
                        {
                            "vehicleId": "synthetic",
                            "type": "EV",
                            "vehicleName": "IONIQ",
                            "nickname": "My car",
                        }
                    ]
                }
            )
        ),
        response(
            envelope(
                {
                    "vinInfo": [
                        {"basic": {"vin": "SYNTHETIC_VIN", "ccuCCS2ProtocolSupport": 0}}
                    ]
                }
            )
        ),
    ]
    vehicles = api.get_vehicles(Token(access_token="access", device_id="device"))
    assert len(vehicles) == 1
    assert vehicles[0].VIN == "SYNTHETIC_VIN"
    assert str(vehicles[0].timezone) == "Asia/Tokyo"
    assert api.session.request.call_args.args[1].endswith("/synthetic/profile")


@pytest.mark.parametrize("protocol", [None, "0", -1])
def test_discovery_does_not_guess_protocol(api, protocol):
    api.session.request.side_effect = [
        response(envelope({"vehicles": [{"vehicleId": "synthetic"}]})),
        response(
            envelope({"vinInfo": [{"basic": {"ccuCCS2ProtocolSupport": protocol}}]})
        ),
    ]
    with pytest.raises(InvalidAPIResponseError):
        api.get_vehicles(Token())


def test_legacy_cached_wrapper_single_request(api):
    api.session.request.return_value = response(
        envelope(
            {
                "vehicleStatusInfo": {
                    "vehicleStatus": {
                        "time": "20261013090000",
                        "doorLock": True,
                        "battery": {"batSoc": 80},
                    },
                    "vehicleLocation": None,
                }
            }
        )
    )
    vehicle = Vehicle(id="synthetic", ccu_ccs2_protocol_support=0)
    api.update_vehicle_with_cached_state(Token(), vehicle)
    assert vehicle.is_locked is True
    assert vehicle.car_battery_percentage == 80
    assert vehicle.last_updated_at.utcoffset() == dt.timedelta(hours=9)
    assert api.session.request.call_args_list[0].args[1].endswith("/status/latest")


def test_ccs2_cached_single_request(api):
    api.session.request.return_value = response(
        envelope(
            {
                "state": {
                    "Vehicle": {
                        "Date": "20261013090000",
                        "Drivetrain": {
                            "FuelSystem": {"DTE": {"Total": 300, "Unit": 1}}
                        },
                        "Green": {
                            "BatteryManagement": {"BatteryRemain": {"Ratio": 65}}
                        },
                    }
                }
            }
        )
    )
    vehicle = Vehicle(id="synthetic", ccu_ccs2_protocol_support=1)
    api.update_vehicle_with_cached_state(Token(), vehicle)
    assert vehicle.ev_battery_percentage == 65
    assert vehicle.total_driving_range == 300
    assert vehicle.total_driving_range_unit == "km"
    assert vehicle.last_updated_at.hour == 18
    assert vehicle.last_updated_at.utcoffset() == dt.timedelta(hours=9)
    assert (
        api.session.request.call_args_list[0].args[1].endswith("/ccs2/carstatus/latest")
    )


@pytest.mark.parametrize(
    "payload",
    [{}, {"vehicleStatusInfo": {}}, {"vehicleStatusInfo": {"vehicleStatus": None}}],
)
def test_malformed_status_rejected(api, payload):
    api.session.request.return_value = response(envelope(payload))
    vehicle = Vehicle(id="synthetic", ccu_ccs2_protocol_support=0)
    with pytest.raises(InvalidAPIResponseError):
        api.update_vehicle_with_cached_state(Token(), vehicle)
    assert vehicle.last_updated_at is None


@pytest.mark.parametrize(
    "status,error", [(401, AuthenticationError), (429, RateLimitingError)]
)
def test_http_errors_do_not_expose_payload(api, caplog, status, error):
    secret = "UNIQUE_SYNTHETIC_SECRET_VIN_COORDINATES"
    api.session.request.return_value = response({"resMsg": secret}, status)
    with caplog.at_level(logging.DEBUG), pytest.raises(error) as raised:
        api._request("POST", "/api/v1/user/signin", json={"password": secret})
    assert secret not in caplog.text + str(raised.value)


def test_connection_errors_do_not_expose_url_or_tokens(api, caplog):
    secret = "UNIQUE_SYNTHETIC_PASSWORD_TOKEN_VIN_COORDINATES"
    api.session.request.side_effect = requests.exceptions.Timeout(secret)
    with caplog.at_level(logging.DEBUG), pytest.raises(RequestTimeoutError) as raised:
        api._request("GET", "/api/v1/spa/vehicles/" + secret)
    assert secret not in caplog.text + str(raised.value)


@pytest.mark.parametrize("status", [307, 308])
def test_api_redirect_does_not_forward_secrets(api, status):
    redirect = response({}, status)
    redirect.headers = {"Location": "https://example.invalid/steal"}
    api.session.request.return_value = redirect
    with pytest.raises(APIError):
        api._request("POST", "/api/v1/user/signin", json={"password": "synthetic"})
    api.session.request.assert_called_once()
    assert api.session.request.call_args.kwargs["allow_redirects"] is False


@pytest.mark.parametrize(
    "identifier,protocol", [("../x", 0), ("x%2Fy", 0), ("x", True), ("x", -1)]
)
def test_cached_arguments_validated_before_io(api, identifier, protocol):
    with pytest.raises(InvalidAPIResponseError):
        api.update_vehicle_with_cached_state(
            Token(), Vehicle(id=identifier, ccu_ccs2_protocol_support=protocol)
        )
    api.session.request.assert_not_called()


def test_parser_failure_does_not_partially_mutate_vehicle(api):
    vehicle = Vehicle(id="synthetic", ccu_ccs2_protocol_support=1)
    api.session.request.return_value = response(
        envelope(
            {
                "state": {
                    "Vehicle": {
                        "Date": "20261013090000",
                        "Electronics": {"Battery": {"Level": 90}},
                    }
                }
            }
        )
    )
    with pytest.raises(InvalidAPIResponseError):
        api.update_vehicle_with_cached_state(Token(), vehicle)
    assert vehicle.last_updated_at is None
    assert vehicle.car_battery_percentage is None


def test_legacy_temperature_does_not_guess_profile_dependent_scale(api):
    api.session.request.return_value = response(
        envelope(
            {
                "vehicleStatusInfo": {
                    "vehicleStatus": {
                        "time": "20261013090000",
                        "airTemp": {
                            "value": "0EH",
                            "unit": 0,
                            "hvacTempType": None,
                        },
                    },
                }
            }
        )
    )
    vehicle = Vehicle(id="synthetic", ccu_ccs2_protocol_support=0)
    api.update_vehicle_with_cached_state(Token(), vehicle)
    assert vehicle.air_temperature is None


def test_unknown_error_code_does_not_become_success(api):
    api.session.request.return_value = response({"resCode": "unrecognized"})
    with pytest.raises(APIError):
        api.get_vehicles(Token())


def test_authorize_external_redirect_rejected_before_signin(api):
    redirect = response({}, 302)
    redirect.url = api.BASE_URL + "/api/v1/user/oauth2/authorize"
    redirect.headers = {"Location": "https://example.invalid/login"}
    api.session.request.return_value = redirect
    with pytest.raises(AuthenticationError):
        api.login("synthetic", "synthetic")
    api.session.request.assert_called_once()


def test_legacy_zero_and_false_status_are_preserved(api):
    api.session.request.return_value = response(
        envelope(
            {
                "vehicleStatusInfo": {
                    "vehicleStatus": {
                        "time": "20261013090000",
                        "doorLock": False,
                        "engine": False,
                        "battery": {"batSoc": 0},
                        "evStatus": {"batteryStatus": 0, "batteryCharge": False},
                    },
                }
            }
        )
    )
    vehicle = Vehicle(id="synthetic", ccu_ccs2_protocol_support=0)
    api.update_vehicle_with_cached_state(Token(), vehicle)
    assert vehicle.is_locked is False
    assert vehicle.engine_is_running is False
    assert vehicle.car_battery_percentage == 0
    assert vehicle.ev_battery_percentage == 0
    assert vehicle.ev_battery_is_charging is False


def test_empty_account_discovery(api):
    api.session.request.return_value = response(envelope({"vehicles": []}))
    assert api.get_vehicles(Token()) == []
    api.session.request.assert_called_once()


def test_discovery_keeps_multiple_vehicles_separate(api):
    api.session.request.side_effect = [
        response(
            envelope(
                {
                    "vehicles": [
                        {"vehicleId": "first", "nickname": "First", "type": "EV"},
                        {"vehicleId": "second", "nickname": "Second", "type": "EV"},
                    ]
                }
            )
        ),
        response(envelope({"vinInfo": [{"basic": {"ccuCCS2ProtocolSupport": 0}}]})),
        response(envelope({"vinInfo": [{"basic": {"ccuCCS2ProtocolSupport": 1}}]})),
    ]
    vehicles = api.get_vehicles(Token())
    assert [v.id for v in vehicles] == ["first", "second"]
    assert [v.ccu_ccs2_protocol_support for v in vehicles] == [0, 1]


@pytest.mark.parametrize(
    "payload,error",
    [
        ({"error": "invalid_grant"}, AuthenticationError),
        ({"error": "invalid_token"}, AuthenticationError),
        ({"resCode": "4003"}, APIError),
        ({"error": "invalid_request"}, APIError),
    ],
)
def test_bad_request_classifies_only_known_auth_failures(api, payload, error):
    api.session.request.return_value = response(payload, 400)
    with pytest.raises(error) as raised:
        api.refresh_access_token(Token(refresh_token="synthetic"))
    assert type(raised.value) is error


def test_language_accepts_empty_204_without_decoding_json(api):
    language_response = response(None, 204)
    language_response.json.side_effect = requests.exceptions.JSONDecodeError(
        "empty response", "", 0
    )
    api.session.request.side_effect = [
        response(None),
        language_response,
        response({"redirectUrl": api.REDIRECT_URI + "?code=SYNTHETIC_CODE"}),
        response(
            {
                "access_token": "SYNTHETIC_ACCESS",
                "token_type": "Bearer",
                "refresh_token": "SYNTHETIC_REFRESH",
                "expires_in": 3600,
            }
        ),
        response(envelope({"deviceId": "SYNTHETIC_DEVICE"})),
    ]
    assert api.login("synthetic", "synthetic").device_id == "SYNTHETIC_DEVICE"
    language_response.json.assert_not_called()


def test_requests_json_decode_error_has_response_classification(api):
    bad_response = response(None)
    bad_response.json.side_effect = requests.exceptions.JSONDecodeError(
        "invalid", "UNIQUE_SYNTHETIC_SECRET", 0
    )
    api.session.request.return_value = bad_response
    with pytest.raises(InvalidAPIResponseError) as raised:
        api.get_vehicles(Token())
    assert "UNIQUE_SYNTHETIC_SECRET" not in str(raised.value)


@pytest.mark.parametrize(
    "range_option,hvac_type,code,expected",
    [
        (0, 1, "06H", 17.0),
        (0, 1, "1AH", 27.0),
        (1, 1, "03H", 15.5),
        (1, 1, "1FH", 29.5),
        (None, 4, "02H", 15.0),
        (None, 4, "20H", 30.0),
        (0, 1, "00H", None),
        (0, 1, "FEH", None),
        (None, None, "06H", None),
        (0, 1, "secret", None),
    ],
)
def test_profile_dependent_legacy_temperature(
    api, range_option, hvac_type, code, expected
):
    api._vehicle_profiles["synthetic"] = {
        "option": {"climateTempRangeOption": range_option}
    }
    assert (
        api._legacy_temperature(
            Vehicle(id="synthetic"),
            {
                "value": code,
                "unit": 0,
                "hvacTempType": hvac_type,
            },
        )
        == expected
    )


@pytest.mark.parametrize("wrapped", [True, False])
def test_stored_location_including_zero_coordinates(api, wrapped):
    payload = {"coord": {"lat": 0, "lon": 0}, "time": "20261014090000"}
    api.session.request.return_value = response(
        envelope({"gpsDetail": payload} if wrapped else payload)
    )
    vehicle = Vehicle(id="synthetic")
    api.update_vehicle_with_cached_location(Token(), vehicle)
    assert vehicle.location_latitude == 0
    assert vehicle.location_longitude == 0
    assert vehicle.location_last_updated_at.utcoffset() == dt.timedelta(hours=9)
    assert api.session.request.call_args.args[1].endswith("/location/park")


@pytest.mark.parametrize(
    "latitude,longitude", [(91, 0), (0, 181), (float("nan"), 0), (True, 0)]
)
def test_invalid_parking_coordinates_do_not_mutate_vehicle(api, latitude, longitude):
    api.session.request.return_value = response(
        envelope(
            {"coord": {"lat": latitude, "lon": longitude}, "time": "20261014090000"}
        )
    )
    vehicle = Vehicle(id="synthetic")
    with pytest.raises(InvalidAPIResponseError):
        api.update_vehicle_with_cached_location(Token(), vehicle)
    assert vehicle.location_latitude is None


def test_stored_energy_totals_preserve_zero(api):
    api.session.request.return_value = response(
        envelope(
            {
                "drivingInfo": [
                    {
                        "drivingPeriod": 0,
                        "totalPwrCsp": 0,
                        "regenPwr": 123.5,
                    }
                ]
            }
        )
    )
    vehicle = Vehicle(id="synthetic")
    api.update_vehicle_with_driving_info(Token(), vehicle)
    assert vehicle.total_power_consumed == 0
    assert vehicle.total_power_regenerated == 123.5
    assert api.session.request.call_args.kwargs["json"] == {"periodTarget": 0}


def test_invalid_energy_totals_do_not_mutate_vehicle(api):
    api.session.request.return_value = response(
        envelope(
            {
                "drivingInfo": [
                    {
                        "drivingPeriod": 0,
                        "totalPwrCsp": 1,
                        "regenPwr": -1,
                    }
                ]
            }
        )
    )
    vehicle = Vehicle(id="synthetic")
    with pytest.raises(InvalidAPIResponseError):
        api.update_vehicle_with_driving_info(Token(), vehicle)
    assert vehicle.total_power_consumed is None


def test_profile_camera_capability_survives_vehicle_manager_initialization(
    api, monkeypatch
):
    api._vehicle_profiles["supported"] = {"option": {"svmOptionInfo": 1}}
    api._vehicle_profiles["unsupported"] = {"option": {"svmOptionInfo": 0}}
    vehicles = [Vehicle(id="supported"), Vehicle(id="unsupported")]
    monkeypatch.setattr(api, "get_vehicles", lambda token: vehicles)
    monkeypatch.setattr(
        VehicleManager, "get_implementation_by_region_brand", lambda *args: api
    )
    manager = VehicleManager(
        10, 2, "synthetic", "synthetic", "1234", geocode_api_enable=False
    )
    manager.initialize_vehicles()
    assert manager.vehicles["supported"].supports_svm is True
    assert manager.vehicles["unsupported"].supports_svm is False


def test_optional_auth_failure_does_not_publish_partial_status(api):
    api.session.request.side_effect = [
        response(
            envelope(
                {
                    "vehicleStatusInfo": {
                        "vehicleStatus": {
                            "time": "20261014090000",
                            "doorLock": True,
                        }
                    }
                }
            )
        ),
        response({}, 401),
    ]
    vehicle = Vehicle(id="synthetic", ccu_ccs2_protocol_support=0)
    with pytest.raises(AuthenticationError):
        api.update_vehicle_with_cached_state(Token(), vehicle)
    assert vehicle.is_locked is None
    assert vehicle.last_updated_at is None


def test_missing_parking_data_is_not_requested_each_poll(api):
    api.session.request.return_value = response(
        envelope(
            {
                "vehicleStatusInfo": {
                    "vehicleStatus": {"time": "20261014090000"},
                }
            }
        )
    )
    vehicle = Vehicle(id="synthetic", ccu_ccs2_protocol_support=0)
    api.update_vehicle_with_cached_state(Token(), vehicle)
    api.update_vehicle_with_cached_state(Token(), vehicle)
    assert (
        sum(
            call.args[1].endswith("/location/park")
            for call in api.session.request.call_args_list
        )
        == 1
    )


def test_mqtt_registration_uuid_token_roundtrip_and_refresh(api):
    token = Token(
        refresh_token="refresh", mqtt_registration_uuid="synthetic-registration"
    )
    restored = Token.from_dict(token.to_dict())
    assert restored.mqtt_registration_uuid == "synthetic-registration"
    api.session.request.return_value = response(
        {"token_type": "Bearer", "access_token": "access", "expires_in": 600}
    )
    assert (
        api.refresh_access_token(restored).mqtt_registration_uuid
        == "synthetic-registration"
    )


def test_force_refresh_requires_correlated_completion_and_newer_state(api, monkeypatch):
    helper = Mock()
    helper.prepare.side_effect = lambda: setattr(
        helper,
        "request_context",
        {"clientId": "synthetic-client", "vehicleId": "synthetic-broker"},
    )
    helper.status.return_value = ORDER_STATUS.SUCCESS
    helper.latest_state = {
        "Date": dt.datetime.now(dt.UTC).strftime("%Y%m%d%H%M%S"),
        "Drivetrain": {"FuelSystem": {"DTE": {"Total": 321, "Unit": 1}}},
        "Green": {"BatteryManagement": {"BatteryRemain": {"Ratio": 70}}},
    }
    monkeypatch.setattr(api, "_mqtt_for_vehicle", lambda *args: helper)
    monkeypatch.setattr(
        api,
        "_control_headers",
        lambda token, vehicle=None: {"Authorization": "synthetic"},
    )
    api.session.request.return_value = response({"msgId": "synthetic-action"})
    vehicle = Vehicle(id="synthetic", ccu_ccs2_protocol_support=1)
    vehicle.last_updated_at = dt.datetime(2026, 10, 13, tzinfo=dt.UTC)
    api.force_refresh_vehicle_state(Token(pin="1234"), vehicle)
    helper.prepare.assert_called_once()
    helper.track.assert_called_once_with("synthetic-action")
    assert vehicle.ev_battery_percentage == 70
    assert vehicle.total_driving_range == 321
    assert api.session.request.call_args.args[1].endswith("/ccs2/carstatus")
    headers = api.session.request.call_args.kwargs["headers"]
    assert headers["clientId"] == "synthetic-client"
    assert headers["vehicleId"] == "synthetic-broker"
    assert headers["ccuCCS2ProtocolSupport"] == "1"
    assert headers["Authorization"] == "synthetic"


@pytest.mark.parametrize(
    "status,error",
    [(ORDER_STATUS.UNKNOWN, RequestTimeoutError), (ORDER_STATUS.FAILED, APIError)],
)
def test_force_refresh_ack_does_not_mean_success(api, monkeypatch, status, error):
    helper = Mock()
    helper.status.return_value = status
    monkeypatch.setattr(api, "_mqtt_for_vehicle", lambda *args: helper)
    monkeypatch.setattr(
        api,
        "_control_headers",
        lambda token, vehicle=None: {"Authorization": "synthetic"},
    )
    api.session.request.return_value = response({"msgId": "synthetic-action"})
    vehicle = Vehicle(id="synthetic", ccu_ccs2_protocol_support=1)
    with pytest.raises(error):
        api.force_refresh_vehicle_state(Token(pin="1234"), vehicle)
    assert vehicle.last_updated_at is None


def test_confirmed_refresh_without_new_telemetry_times_out(api, monkeypatch):
    helper = Mock()
    helper.status.return_value = ORDER_STATUS.SUCCESS
    helper.latest_state = None
    monkeypatch.setattr(api, "_mqtt_for_vehicle", lambda *args: helper)
    monkeypatch.setattr(
        api,
        "_control_headers",
        lambda token, vehicle=None: {"Authorization": "synthetic"},
    )
    monkeypatch.setattr(api, "update_vehicle_with_cached_state", lambda *args: None)
    api.FORCE_REFRESH_TIMEOUT = 0.001
    api.session.request.return_value = response({"msgId": "synthetic-action"})
    vehicle = Vehicle(id="synthetic", ccu_ccs2_protocol_support=1)
    with pytest.raises(RequestTimeoutError):
        api.force_refresh_vehicle_state(Token(pin="1234"), vehicle)
    assert vehicle.last_updated_at is None


def test_force_ignores_retained_state_and_uses_newer_cached_status(api, monkeypatch):
    helper = Mock()
    helper.status.return_value = ORDER_STATUS.SUCCESS
    helper.latest_state = {"Date": "20261013090000"}
    monkeypatch.setattr(api, "_mqtt_for_vehicle", lambda *args: helper)
    monkeypatch.setattr(
        api,
        "_control_headers",
        lambda token, vehicle=None: {"Authorization": "synthetic"},
    )

    def cached(token, candidate):
        candidate.last_updated_at = dt.datetime.now(dt.UTC)
        candidate.is_locked = True

    monkeypatch.setattr(api, "update_vehicle_with_cached_state", cached)
    api.session.request.return_value = response({"msgId": "synthetic-action"})
    vehicle = Vehicle(id="synthetic", ccu_ccs2_protocol_support=1)
    vehicle.last_updated_at = dt.datetime(2026, 10, 13, 10, tzinfo=dt.UTC)
    api.force_refresh_vehicle_state(Token(pin="1234"), vehicle)
    assert vehicle.last_updated_at > dt.datetime(2026, 10, 13, tzinfo=dt.UTC)
    assert vehicle.is_locked is True


def test_force_completion_before_vss_waits_for_newer_message(api, monkeypatch):
    helper = Mock()
    helper.status.return_value = ORDER_STATUS.SUCCESS
    new_state = {
        "Date": dt.datetime.now(dt.UTC).strftime("%Y%m%d%H%M%S"),
        "Drivetrain": {"FuelSystem": {"DTE": {"Total": 321, "Unit": 1}}},
    }
    monkeypatch.setattr(
        type(helper),
        "latest_state",
        PropertyMock(side_effect=[None, new_state]),
        raising=False,
    )
    monkeypatch.setattr(api, "_mqtt_for_vehicle", lambda *args: helper)
    monkeypatch.setattr(
        api,
        "_control_headers",
        lambda token, vehicle=None: {"Authorization": "synthetic"},
    )
    monkeypatch.setattr(api, "update_vehicle_with_cached_state", lambda *args: None)
    monkeypatch.setattr("time.sleep", lambda *args: None)
    api.session.request.return_value = response({"msgId": "synthetic-action"})
    vehicle = Vehicle(id="synthetic", ccu_ccs2_protocol_support=1)
    vehicle.last_updated_at = dt.datetime(2026, 10, 13, tzinfo=dt.UTC)
    api.force_refresh_vehicle_state(Token(pin="1234"), vehicle)
    assert vehicle.last_updated_at > dt.datetime(2026, 10, 13, tzinfo=dt.UTC)
    assert vehicle.total_driving_range == 321


def test_charge_target_mapping_and_cache(api):
    api.session.request.return_value = response(
        envelope(
            {
                "targetSOClist": [
                    {"plugType": 0, "targetSOClevel": 80},
                    {"plugType": 1, "targetSOClevel": 90},
                ]
            }
        )
    )
    vehicle = Vehicle(id="synthetic")
    api.update_vehicle_with_charge_targets(Token(), vehicle)
    assert vehicle.ev_charge_limits_dc == 80
    assert vehicle.ev_charge_limits_ac == 90
    assert api._charge_target_cache[vehicle.id][1] == {0: 80, 1: 90}
    assert api.session.request.call_args.args[1].endswith("/charge/target")


@pytest.mark.parametrize(
    "entries",
    [
        [{"plugType": True, "targetSOClevel": 90}],
        [{"plugType": 0, "targetSOClevel": 101}],
        [{"plugType": 0, "targetSOClevel": 80}, {"plugType": 0, "targetSOClevel": 90}],
    ],
)
def test_invalid_charge_targets_are_transactional(api, entries):
    api.session.request.return_value = response(envelope({"targetSOClist": entries}))
    vehicle = Vehicle(id="synthetic", ev_charge_limits_dc=70)
    with pytest.raises(InvalidAPIResponseError):
        api.update_vehicle_with_charge_targets(Token(), vehicle)
    assert vehicle.ev_charge_limits_dc == 70
    assert vehicle.id not in api._charge_target_cache


def test_charge_targets_preserve_alternate_ac_plug_type(api):
    api.session.request.return_value = response(
        envelope(
            {
                "targetSOClist": [
                    {"plugType": 0, "targetSOClevel": 80},
                    {"plugType": 2, "targetSOClevel": 90},
                ]
            }
        )
    )
    vehicle = Vehicle(id="synthetic")
    api.update_vehicle_with_charge_targets(Token(), vehicle)
    assert vehicle.ev_charge_limits_ac == 90
    assert api._charge_target_cache[vehicle.id][1] == {0: 80, 2: 90}


def test_rhd_ccs2_physical_positions_only_front_fields_swap(api):
    api._vehicle_profiles["synthetic"] = {"option": {"drvSeatLoc": "R"}}
    vehicle = Vehicle(id="synthetic")
    api._update_vehicle_properties_ccs2(
        vehicle,
        {
            "Date": "20261014090000",
            "Drivetrain": {"FuelSystem": {"DTE": {"Total": 300, "Unit": 1}}},
            "Cabin": {
                "Seat": {
                    "Row1": {
                        "Driver": {"Climate": {"State": 6}},
                        "Passenger": {"Climate": {"State": 3}},
                    },
                    "Row2": {
                        "Left": {"Climate": {"State": 7}},
                        "Right": {"Climate": {"State": 4}},
                    },
                },
                "Door": {
                    "Row1": {
                        "Driver": {"Open": True, "Lock": 0},
                        "Passenger": {"Open": False, "Lock": 1},
                    },
                    "Row2": {"Left": {"Open": True}, "Right": {"Open": False}},
                },
                "Window": {"Row1": {"Driver": {"Open": 1}, "Passenger": {"Open": 0}}},
            },
        },
    )
    assert vehicle.front_right_seat_status == "Low Heat"
    assert vehicle.front_left_seat_status == "Low Cool"
    assert vehicle.rear_left_seat_status == "Medium Heat"
    assert vehicle.rear_right_seat_status == "Medium Cool"
    assert vehicle.front_right_door_is_open is True
    assert vehicle.front_left_door_is_open is False
    assert vehicle.front_right_door_is_locked is True
    assert vehicle.front_left_door_is_locked is False
    assert vehicle.back_left_door_is_open is True
    assert vehicle.back_right_door_is_open is False
    assert vehicle.front_right_window_is_open is True
    assert vehicle.front_left_window_is_open is False


def test_rhd_legacy_driver_seat_fallback(api):
    api._vehicle_profiles["synthetic"] = {"option": {"drvSeatLoc": "R"}}
    api.session.request.return_value = response(
        envelope(
            {
                "vehicleStatusInfo": {
                    "vehicleStatus": {
                        "time": "20261014090000",
                        "seatHeaterVentState": {
                            "drvSeatHeatState": 6,
                            "astSeatHeatState": 3,
                        },
                    }
                }
            }
        )
    )
    vehicle = Vehicle(id="synthetic", ccu_ccs2_protocol_support=0)
    api.update_vehicle_with_cached_state(Token(), vehicle)
    assert vehicle.front_right_seat_status == "Low Heat"
    assert vehicle.front_left_seat_status == "Low Cool"


def test_daily_energy_rows_use_japan_calendar_dates_and_wh(api):
    api.session.request.return_value = response(
        envelope(
            {
                "drivingInfo": [
                    {"drivingPeriod": 0, "totalPwrCsp": 1000, "regenPwr": 100}
                ],
                "drivingInfoDetail": [
                    {
                        "drivingDate": "20261014",
                        "totalPwrCsp": 0,
                        "motorPwrCsp": 0,
                        "regenPwr": 50,
                        "calculativeOdo": 10,
                    }
                ],
            }
        )
    )
    vehicle = Vehicle(id="synthetic")
    api.update_vehicle_with_driving_info(Token(), vehicle)
    assert len(vehicle.daily_stats) == 1
    day = vehicle.daily_stats[0]
    assert day.date.utcoffset() == dt.timedelta(hours=9)
    assert day.total_consumed == 0
    assert day.regenerated_energy == 50
    assert day.distance == 10 and day.distance_unit == "km"
    assert vehicle.power_consumption_30d is None


@pytest.mark.parametrize(
    "option,expected",
    [
        ({"climateTempRangeOption": 0}, (17.0, 27.0)),
        ({"climateTempRangeOption": 1}, (15.5, 29.5)),
        ({"hvacTempType": 4}, (15.0, 30.0)),
        ({"climateTempRangeOption": True}, None),
        ({}, None),
    ],
)
def test_profile_climate_bounds_for_home_assistant(api, option, expected):
    api._vehicle_profiles["synthetic"] = {"option": option}
    vehicle = Vehicle(id="synthetic")
    api.initialize_vehicle_capabilities(vehicle)
    assert vehicle.climate_temperature_range == expected


def test_front_only_window_capability_preserves_rear_telemetry(api):
    api._vehicle_profiles["synthetic"] = {
        "option": {"windowSafetyOption2": 4},
        "serviceOption": {"windowControlOption": 1},
    }
    vehicle = Vehicle(id="synthetic", back_left_window_is_open=True)
    api.initialize_vehicle_capabilities(vehicle)
    assert vehicle.supported_window_positions == ("front_left", "front_right")
    assert vehicle.back_left_window_is_open is True


def test_old_cached_only_token_requires_reauthentication_without_io(api):
    with pytest.raises(AuthenticationError, match="Reauthenticate"):
        api._mqtt_for_vehicle(Token(), Vehicle(id="synthetic"))
    api.session.request.assert_not_called()


@pytest.mark.parametrize(
    "code,error,label",
    [
        ("5911", NoDataFound, "no data found"),
        ("5921", NoDataFound, "no data found"),
        ("4004", DuplicateRequestError, "duplicate request"),
        ("4292", RateLimitingError, "surround-view capture limit"),
        ("7779", ServiceTemporaryUnavailable, "surround-view unavailable"),
        ("7780", APIError, "camera error"),
        ("7781", APIError, "emergency lights"),
        ("7782", APIError, "low battery"),
    ],
)
def test_public_vendor_diagnostics_are_sanitized_on_http_error(
    api, caplog, code, error, label
):
    secret = "SYNTHETIC_PASSWORD_TOKEN_VIN_COORDINATES"
    api.session.request.return_value = response(
        {"retCode": "F", "resCode": code, "resMsg": secret}, 400
    )
    with caplog.at_level(logging.DEBUG), pytest.raises(error) as raised:
        api._request("POST", "/api/v1/spa/vehicles/synthetic/svm/async")
    assert "HTTP 400" in str(raised.value)
    assert code in str(raised.value) and label in str(raised.value)
    assert secret not in str(raised.value) + caplog.text


def test_unknown_vendor_code_is_not_echoed(api):
    secret = "SYNTHETIC_SECRET_IN_CODE"
    api.session.request.return_value = response(
        {"resCode": secret, "resMsg": secret}, 400
    )
    with pytest.raises(APIError) as raised:
        api._request("POST", "/api/v1/spa/vehicles/synthetic/svm/async")
    assert "HTTP 400" in str(raised.value)
    assert secret not in str(raised.value)


@pytest.mark.parametrize(
    "path,error,label",
    [
        ("/api/v1/user/signin", AuthenticationError, "sign-in"),
        ("/api/v1/user/pin", AuthenticationError, "PIN"),
        ("/api/v1/spa/vehicles/synthetic/svm/async", APIError, "invalid value"),
    ],
)
def test_invalid_value_is_classified_in_request_context(api, path, error, label):
    api.session.request.return_value = response(
        {"retCode": "F", "resCode": "4003"}, 400
    )
    with pytest.raises(error) as raised:
        api._request("POST", path)
    assert type(raised.value) is error
    assert label in str(raised.value)


def test_invalid_request_is_not_assumed_to_be_invalid_device_id(api):
    api.session.request.return_value = response({"resCode": "4002"}, 400)
    with pytest.raises(APIError) as raised:
        api._request("POST", "/api/v1/spa/vehicles/synthetic/svm/async")
    assert type(raised.value) is APIError
    assert "invalid request" in str(raised.value)


def test_vehicle_headers_use_protocol_and_ready_context_only(api):
    token = Token(
        access_token="Bearer synthetic",
        device_id="synthetic-device",
        mqtt_registration_uuid="synthetic-registration",
    )
    vehicle = Vehicle(id="synthetic", ccu_ccs2_protocol_support=1)
    helper = Mock()
    helper.request_context = {
        "clientId": "synthetic-client",
        "vehicleId": "synthetic-broker",
        "Authorization": "forbidden",
    }
    headers = api._vehicle_headers(token, vehicle, helper)
    assert headers["ccuCCS2ProtocolSupport"] == "1"
    assert headers["clientId"] == "synthetic-client"
    assert headers["vehicleId"] == "synthetic-broker"
    assert headers["Authorization"] == "Bearer synthetic"


def test_real_adapter_camera_http_5911_polls_then_returns_image(api, monkeypatch):
    monkeypatch.setattr("time.sleep", lambda seconds: None)
    api.session.request.side_effect = [
        response({"controlToken": "synthetic-control", "expiresTime": 60}),
        response({"retCode": "S", "resCode": "0000", "msgId": "synthetic-capture"}),
        response(
            {
                "retCode": "F",
                "resCode": "5911",
                "resMsg": "SYNTHETIC_PRIVATE_VENDOR_TEXT",
            },
            400,
        ),
        response(envelope({"scsDetail": {"svmImage": "U1lOVEhFVElDX0lNQUdF"}})),
    ]
    token = Token(
        access_token="Bearer synthetic-access",
        device_id="synthetic-device",
        pin="1234",
        mqtt_registration_uuid="synthetic-registration",
    )
    vehicle = Vehicle(id="synthetic", ccu_ccs2_protocol_support=1, supports_svm=True)
    image = api.request_svm_capture(token, vehicle, acknowledged_warning=True)
    assert image.image_bytes == b"SYNTHETIC_IMAGE"
    calls = api.session.request.call_args_list
    assert [call.args[0] for call in calls] == ["PUT", "POST", "GET", "GET"]
    assert all(
        call.kwargs["headers"]["Authorization"] == "Bearer synthetic-access"
        for call in calls[1:]
    )
    assert (
        calls[2].kwargs["params"]
        == calls[3].kwargs["params"]
        == {"msgId": "synthetic-capture"}
    )
    assert api.get_svm_details(token, vehicle) is image


@pytest.mark.parametrize(
    "code,label",
    [
        ("5016", "not uploaded"),
        ("4101", "not registered"),
        ("4140", "not owned"),
        ("4008", "updating"),
        ("4901", "immobilized"),
        ("5311", "stolen-vehicle"),
        ("5152", "GPS"),
        ("4011", "verification"),
        ("4291", "already processed"),
    ],
)
def test_additional_public_server_error_labels_are_fixed(api, code, label):
    secret = "SYNTHETIC_PRIVATE_VENDOR_MESSAGE"
    api.session.request.return_value = response(
        {"retCode": "F", "resCode": code, "resMsg": secret}, 400
    )
    with pytest.raises(APIError) as raised:
        api._request("POST", "/api/v1/spa/vehicles/synthetic/svm/async")
    assert code in str(raised.value) and label in str(raised.value)
    assert secret not in str(raised.value)


def test_force_refresh_renews_control_grant_after_slow_connection(api, monkeypatch):
    clock = [1790000000.0]
    monkeypatch.setattr(
        "hyundai_kia_connect_api.japan_commands.time.time", lambda: clock[0]
    )
    helper = Mock()
    helper.request_context = {}
    helper.prepare.side_effect = lambda: clock.__setitem__(0, 1790000100.0)
    helper.status.return_value = ORDER_STATUS.FAILED
    monkeypatch.setattr(api, "_mqtt_for_vehicle", lambda *args: helper)
    api._request = Mock(
        side_effect=[
            {"controlToken": "synthetic-fresh-grant", "expiresTime": 60},
            {"msgId": "synthetic-action"},
        ]
    )
    token = Token(
        pin="synthetic-pin",
        mqtt_registration_uuid="synthetic-registration",
        device_id="synthetic-device",
        access_token="Bearer synthetic-access",
        control_token="Bearer synthetic-expiring-grant",
        control_token_expiry=1790000001.0,
    )
    vehicle = Vehicle(id="synthetic", ccu_ccs2_protocol_support=1)
    with pytest.raises(APIError, match="refresh failed"):
        api.force_refresh_vehicle_state(token, vehicle)
    grant, refresh = api._request.call_args_list
    assert grant.args == ("PUT", "/api/v1/user/pin")
    assert refresh.args == ("GET", "/api/v2/spa/vehicles/synthetic/ccs2/carstatus")
    assert refresh.kwargs["headers"]["Authorization"] == "Bearer synthetic-fresh-grant"
    helper.close.assert_called_once()
