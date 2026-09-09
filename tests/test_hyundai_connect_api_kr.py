"""Protocol-level tests for Hyundai Korea support."""

import datetime as dt
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlparse

import pytest

from hyundai_kia_connect_api import ClimateRequestOptions, HyundaiConnectApiKR
from hyundai_kia_connect_api.const import ENGINE_TYPES, VEHICLE_LOCK_ACTION
from hyundai_kia_connect_api.exceptions import (
    APIError,
    AuthenticationError,
    PINMissingError,
    ServiceTemporaryUnavailable,
)
from hyundai_kia_connect_api.Token import Token
from hyundai_kia_connect_api.Vehicle import Vehicle
from hyundai_kia_connect_api.VehicleManager import VehicleManager


def _api() -> HyundaiConnectApiKR:
    return HyundaiConnectApiKR(region=10, brand=2, language="ko")


def _token(**overrides) -> Token:
    values = {
        "access_token": "Bearer ccs-token",
        "cci_access_token": "cci-token",
        "non_ccs_token": "non-ccs-token",
        "exchangeable_token": "exchangeable-token",
        "device_id": "12345678-1234-1234-1234-123456789abc",
        "valid_until": dt.datetime.now(dt.UTC) + dt.timedelta(hours=1),
        "pin": "1234",
        "user_id": "profile-id",
        "cc_id": "customer-id",
    }
    values.update(overrides)
    return Token(**values)


def _vehicle() -> Vehicle:
    vehicle = Vehicle()
    vehicle.id = "car-id"
    vehicle.ccu_ccs2_protocol_support = 2
    return vehicle


def _response(payload: dict, status_code: int = 200) -> MagicMock:
    response = MagicMock(status_code=status_code)
    response.json.return_value = payload
    return response


def test_vehicle_manager_routes_hyundai_korea():
    api = VehicleManager.get_implementation_by_region_brand(10, 2, "ko")

    assert isinstance(api, HyundaiConnectApiKR)


def test_vehicle_manager_rejects_non_hyundai_korea():
    with pytest.raises(APIError, match="Unknown brand Kia for region Korea"):
        VehicleManager.get_implementation_by_region_brand(10, 1, "ko")


def test_korean_cci_headers_identify_android_app():
    headers = _api()._get_cci_headers("device-id")

    assert headers["client-id"] == "com.hyundai.oneapp.kr"
    assert headers["client-version"] == "1.6.0"
    assert headers["client-os-code"] == "AOS"
    assert headers["client-device-model"] == "Android"
    assert headers["client-notification-provider-type"] == "FCM"
    assert headers["locale"] == "KO"


def test_korean_login_uses_pleos_oauth_parameters_and_oaep_encryption():
    api = _api()
    authorize_response = MagicMock(text="", url="https://idpconnect-kr.hyundai.com")
    cert_response = _response(
        {
            "retValue": {
                "kid": "test-kid",
                "n": "AQ",
                "e": "AQAB",
                "alg": "RSA-OAEP-256",
            }
        }
    )
    signin_response = MagicMock(status_code=400, text="stopped after signin")
    session = MagicMock()
    session.get.side_effect = [authorize_response, cert_response]
    session.post.return_value = signin_response
    cipher = MagicMock()
    cipher.encrypt.return_value = b"encrypted"

    with (
        patch(
            "hyundai_kia_connect_api.GspaApiEU.ApiImplSession",
            return_value=session,
        ),
        patch("hyundai_kia_connect_api.GspaApiEU.RSA.construct"),
        patch(
            "hyundai_kia_connect_api.GspaApiEU.PKCS1_OAEP.new",
            return_value=cipher,
        ),
        pytest.raises(AuthenticationError, match="Signin failed"),
    ):
        api._login_with_password("user@example.com", "password", "device-id")

    authorize_url = session.get.call_args_list[0].args[0]
    assert authorize_url.startswith(
        "https://idpconnect-kr.hyundai.com/auth/api/v2/user/oauth2/authorize"
    )
    assert "state=hmgoneapp" in authorize_url
    assert "country=" not in authorize_url
    assert "scope=account.token.transfer%20account.id.generate" in authorize_url
    assert "%20offline%20" in authorize_url
    assert "lang=" not in authorize_url
    signin_data = session.post.call_args.kwargs["data"]
    assert signin_data["state"] == "hmgoneapp"
    assert signin_data["scope"] == api.LOGIN_SCOPE
    assert signin_data["encryptedPassword"] == "true"


def test_korean_browser_authorization_url_uses_pleos():
    api = _api()

    parsed = urlparse(api.get_authorization_url())
    query = parse_qs(parsed.query)

    assert parsed.netloc == "idpconnect-kr.hyundai.com"
    assert parsed.path == "/auth/api/v2/user/oauth2/authorize"
    assert query == {
        "response_type": ["code"],
        "client_id": [api.ONEAPP_CLIENT_ID],
        "redirect_uri": [api.ONEAPP_REDIRECT_URI],
        "state": ["hmgoneapp"],
        "scope": [api.LOGIN_SCOPE],
    }


def test_korean_capabilities_initialize_omitted_steering_option():
    api = _api()
    token = _token()
    vehicle = Vehicle(id="vehicle-id")
    api._domestic_post = MagicMock(
        return_value={
            "appMode": "GEN2",
            "startYn": "Y",
            "strgWhlHeatingOption": 1,
        }
    )
    api._ensure_cc_id = MagicMock(return_value="connected-car-id")
    api._ensure_user_id = MagicMock(return_value="user-id")

    api.get_vehicle_capabilities(token, vehicle)

    assert vehicle.steering_wheel_heater_option is None
    assert vehicle.steering_wheel_heating_option == 1
    assert vehicle.supports_steering_wheel_heater is True


def test_korean_browser_redirect_exchanges_code_without_storing_password():
    api = _api()
    api._exchange_auth_code_for_cci_tokens = MagicMock(
        return_value={
            "accessToken": "cci-token",
            "refreshToken": "cci-refresh",
            "exchangeableAccessToken": "exchangeable-token",
            "exchangeableRefreshToken": "exchangeable-refresh",
            "nonCcsToken": "non-ccs-token",
            "nonCcsRefreshToken": "non-ccs-refresh",
            "idToken": "id-token",
        }
    )
    valid_until = dt.datetime.now(dt.UTC) + dt.timedelta(hours=1)
    api._exchange_ccs_token = MagicMock(return_value=("ccs-token", valid_until))
    api._register_device = MagicMock()
    api._fetch_user_id = MagicMock()

    token = api.login_with_redirect_url(
        "https://oneapp.hyundai.com/redirect?code=browser-code&state=hmgoneapp",
        pin="1234",
        device_id="device-id",
    )

    assert token.access_token == "Bearer ccs-token"
    assert token.password is None
    assert token.pin == "1234"
    assert token.device_id == "device-id"
    api._exchange_auth_code_for_cci_tokens.assert_called_once_with(
        "device-id", "browser-code"
    )
    api._register_device.assert_called_once_with(token)
    api._fetch_user_id.assert_called_once_with(token)


def test_korean_refresh_matches_myhyundai_request_shape():
    api = _api()
    token = _token(
        refresh_token="cci-refresh-token",
        exchangeable_refresh_token="exchangeable-refresh-token",
        non_ccs_refresh_token="non-ccs-refresh-token",
        id_token="id-token-not-sent-during-refresh",
    )
    response = _response(
        {
            "accessToken": "new-cci-token",
            "refreshToken": "new-cci-refresh-token",
            "exchangeableAccessToken": "new-exchangeable-token",
            "exchangeableRefreshToken": "new-exchangeable-refresh-token",
            "nonCcsToken": "new-non-ccs-token",
            "nonCcsRefreshToken": "new-non-ccs-refresh-token",
            "expiresIn": 3600,
        }
    )
    response.headers = {}
    api._exchange_ccs_token = MagicMock(
        return_value=(
            "new-ccs-token",
            dt.datetime.now(dt.UTC) + dt.timedelta(hours=1),
        )
    )

    with patch(
        "hyundai_kia_connect_api.GspaApiEU.requests.post", return_value=response
    ) as request:
        api._refresh_cci_token(token)

    assert request.call_args.kwargs["json"] == {
        "accessToken": "cci-token",
        "refreshToken": "cci-refresh-token",
        "exchangeableAccessToken": "exchangeable-token",
        "exchangeableRefreshToken": "exchangeable-refresh-token",
        "nonCcsToken": "non-ccs-token",
        "nonCcsRefreshToken": "non-ccs-refresh-token",
    }
    headers = request.call_args.kwargs["headers"]
    assert "authorization" not in headers
    assert "exchangeable-token" not in headers
    assert "Authentication" not in headers
    assert headers["non-ccs-token"] == "non-ccs-token"


def test_korean_refresh_never_falls_back_to_missing_password():
    api = _api()
    api._refresh_cci_token = MagicMock(
        side_effect=AuthenticationError("CCI token refresh failed: HTTP 401")
    )
    api.login = MagicMock()

    with pytest.raises(AuthenticationError, match="HTTP 401"):
        api.refresh_access_token(_token(username=None, password=None))

    api.login.assert_not_called()


def test_korean_browser_redirect_rejects_wrong_state():
    with pytest.raises(AuthenticationError, match="state"):
        _api().login_with_redirect_url(
            "https://oneapp.hyundai.com/redirect?code=browser-code&state=wrong"
        )


def test_korean_domestic_headers_include_vehicle_ccs2_support():
    headers = _api()._get_authenticated_headers(_token(), ccs2_support=2)

    assert headers["Authorization"] == "Bearer ccs-token"
    assert headers["ccsp-service-id"] == "25fa8900-60b0-4f5d-802b-04c7168f64ea"
    assert headers["ccsp-application-id"] == ("a8e416c3-5832-4f70-9f9d-7ecbfc8d96ca")
    assert "X-Request-Id" in headers
    assert "X-Stamp" not in headers
    assert headers["ccuCCS2ProtocolSupport"] == "2"


def test_fetch_user_id_stores_connected_car_id():
    api = _api()
    token = _token(cc_id=None)
    response = _response({"id": "connected-car-id"})

    with patch(
        "hyundai_kia_connect_api.HyundaiConnectApiKR.requests.get",
        return_value=response,
    ) as request:
        api._fetch_user_id(token)

    assert token.cc_id == "connected-car-id"
    assert request.call_args.args[0].endswith("/domain/api/v1/ccsp/me")


def test_cached_status_uses_domestic_ccs2_endpoint_and_payload():
    api = _api()
    token = _token()
    vehicle = _vehicle()
    response = _response(
        {
            "RetCode": "S",
            "resCode": "0000",
            "ServiceNo": "F39",
            "state": {"Vehicle": {"Date": "20260908120000"}},
        }
    )
    api._update_vehicle_properties_ccs2 = MagicMock()

    with patch(
        "hyundai_kia_connect_api.HyundaiConnectApiKR.requests.post",
        return_value=response,
    ) as request:
        api.update_vehicle_with_cached_state(token, vehicle)

    assert request.call_args.args[0].endswith(
        "/api/v1/apps/spa/tmc_new/ccsp/recentcarstatus_ccs2.do"
    )
    assert request.call_args.kwargs["json"] == {
        "CCID": "customer-id_BLU",
        "carID": "car-id",
        "ServiceNo": "F39",
    }
    assert "ccuCCS2ProtocolSupport" not in request.call_args.kwargs["headers"]
    api._update_vehicle_properties_ccs2.assert_called_once_with(
        vehicle, {"Date": "20260908120000"}
    )


def test_vehicle_capabilities_uses_current_myhyundai_infolist_request():
    api = _api()
    token = _token()
    vehicle = _vehicle()
    response = _response(
        {
            "RetCode": "S",
            "resCode": "0000",
            "ServiceNo": "C3",
            "ccuCCS2ProtocolSupport": 3,
            "windowControlOption": 1,
            "appMode": "GEN2",
            "startYn": "Y",
            "remoteControlTime": 168,
            "hvacTempType": 1,
            "seatHeaterVentInfo": [
                {
                    "drvSeatHeatState": 6,
                    "astSeatHeatState": 2,
                    "rlSeatHeatState": 0,
                    "rrSeatHeatState": 0,
                }
            ],
            "strgWhlHeatOption": 1,
            "strgWhlHeatingOption": 1,
        }
    )

    with patch(
        "hyundai_kia_connect_api.HyundaiConnectApiKR.requests.post",
        return_value=response,
    ) as request:
        result = api.get_vehicle_capabilities(token, vehicle)

    assert request.call_args.args[0].endswith(
        "/api/v1/apps/spa/tmc_new/ccsp/infolist_v2.do"
    )
    assert request.call_args.kwargs["json"] == {
        "CCID": "customer-id_BLU",
        "autoLoginYn": "Y",
        "carID": "car-id",
        "profileIndex": "1",
        "userID": "profile-id",
        "ServiceNo": "C3",
    }
    assert "ccuCCS2ProtocolSupport" not in request.call_args.kwargs["headers"]
    assert result["windowControlOption"] == 1
    assert vehicle.ccu_ccs2_protocol_support == 3
    assert vehicle.remote_control_generation == "GEN2"
    assert vehicle.supports_remote_start is True
    assert vehicle.remote_control_waiting_time == 168
    assert vehicle.hvac_temperature_type == 1
    assert vehicle.front_left_seat_climate_capability == 6
    assert vehicle.front_right_seat_climate_capability == 2
    assert vehicle.steering_wheel_heater_option == 1
    assert vehicle.steering_wheel_heating_option == 1
    assert vehicle.supports_steering_wheel_heater is True


def test_vehicle_capabilities_fetches_missing_profile_id():
    api = _api()
    token = _token(user_id=None)
    vehicle = _vehicle()
    api._fetch_user_id = MagicMock(
        side_effect=lambda value: setattr(value, "user_id", "fetched-id")
    )
    api._domestic_post = MagicMock(return_value={})

    api.get_vehicle_capabilities(token, vehicle)

    api._fetch_user_id.assert_called_once_with(token)
    assert api._domestic_post.call_args.args[2]["userID"] == "fetched-id"


def test_vehicle_capabilities_rejects_missing_profile_id():
    api = _api()
    token = _token(user_id=None)
    api._fetch_user_id = MagicMock()

    with pytest.raises(APIError, match="profile ID"):
        api.get_vehicle_capabilities(token, _vehicle())


def test_force_refresh_waits_for_async_response_then_reads_cache():
    api = _api()
    token = _token()
    vehicle = _vehicle()
    api._domestic_post = MagicMock(return_value={"svcSID": "refresh-request"})
    api.update_vehicle_with_cached_state = MagicMock()

    with patch("hyundai_kia_connect_api.HyundaiConnectApiKR.time.sleep") as sleep:
        api.force_refresh_vehicle_state(token, vehicle)

    api._domestic_post.assert_called_once_with(
        token,
        "api/v1/apps/spa/tmc_new/ccsp/carstatus_ccs2.do",
        {"CCID": "customer-id_BLU", "carID": "car-id", "ServiceNo": "F53"},
        ccs2_support=2,
    )
    sleep.assert_called_once_with(25)
    api.update_vehicle_with_cached_state.assert_called_once_with(token, vehicle)


def test_force_refresh_retries_transient_http_500_from_cached_readback():
    api = _api()
    token = _token()
    vehicle = _vehicle()
    api._update_vehicle_properties_ccs2 = MagicMock()
    responses = [
        _response({"RetCode": "S", "svcSID": "refresh-request"}),
        _response({}, status_code=500),
        _response(
            {
                "RetCode": "S",
                "state": {"Vehicle": {"Date": "20260909133833.000"}},
            }
        ),
    ]

    with (
        patch(
            "hyundai_kia_connect_api.HyundaiConnectApiKR.requests.post",
            side_effect=responses,
        ) as request,
        patch("hyundai_kia_connect_api.HyundaiConnectApiKR.time.sleep") as sleep,
    ):
        api.force_refresh_vehicle_state(token, vehicle)

    assert request.call_count == 3
    assert request.call_args_list[1].args[0].endswith("/recentcarstatus_ccs2.do")
    assert request.call_args_list[2].args[0].endswith("/recentcarstatus_ccs2.do")
    assert [call.args[0] for call in sleep.call_args_list] == [25, 10]
    api._update_vehicle_properties_ccs2.assert_called_once_with(
        vehicle, {"Date": "20260909133833.000"}
    )


def test_force_refresh_retries_successful_but_stale_cached_readback():
    api = _api()
    token = _token()
    vehicle = _vehicle()
    previous_update = dt.datetime(2026, 9, 9, 13, 38, 33, tzinfo=dt.UTC)
    refreshed_update = dt.datetime(2026, 9, 9, 13, 39, 14, tzinfo=dt.UTC)
    vehicle.last_updated_at = previous_update
    api._domestic_post = MagicMock(return_value={"svcSID": "refresh-request"})

    def update_cached_state(_token, updated_vehicle):
        if api.update_vehicle_with_cached_state.call_count == 2:
            updated_vehicle.last_updated_at = refreshed_update

    api.update_vehicle_with_cached_state = MagicMock(side_effect=update_cached_state)

    with patch("hyundai_kia_connect_api.HyundaiConnectApiKR.time.sleep") as sleep:
        api.force_refresh_vehicle_state(token, vehicle)

    assert api.update_vehicle_with_cached_state.call_count == 2
    assert [call.args[0] for call in sleep.call_args_list] == [25, 10]
    assert vehicle.last_updated_at == refreshed_update


def test_force_refresh_rejects_stale_cache_after_all_readback_attempts():
    api = _api()
    token = _token()
    vehicle = _vehicle()
    vehicle.last_updated_at = dt.datetime(2026, 9, 9, 13, 38, 33, tzinfo=dt.UTC)
    api._domestic_post = MagicMock(return_value={"svcSID": "refresh-request"})
    api.update_vehicle_with_cached_state = MagicMock()

    with (
        patch("hyundai_kia_connect_api.HyundaiConnectApiKR.time.sleep") as sleep,
        pytest.raises(ServiceTemporaryUnavailable, match="status did not update"),
    ):
        api.force_refresh_vehicle_state(token, vehicle)

    assert api.update_vehicle_with_cached_state.call_count == 3
    assert [call.args[0] for call in sleep.call_args_list] == [25, 10, 10]


def test_force_refresh_stops_after_three_transient_readback_failures():
    api = _api()
    token = _token()
    vehicle = _vehicle()
    responses = [
        _response({"RetCode": "S", "svcSID": "refresh-request"}),
        _response({}, status_code=500),
        _response({}, status_code=500),
        _response({}, status_code=500),
    ]

    with (
        patch(
            "hyundai_kia_connect_api.HyundaiConnectApiKR.requests.post",
            side_effect=responses,
        ) as request,
        patch("hyundai_kia_connect_api.HyundaiConnectApiKR.time.sleep") as sleep,
        pytest.raises(ServiceTemporaryUnavailable, match="HTTP 500"),
    ):
        api.force_refresh_vehicle_state(token, vehicle)

    assert request.call_count == 4
    assert [call.args[0] for call in sleep.call_args_list] == [25, 10, 10]


def test_korean_ccs2_status_date_is_utc_and_displayed_in_vehicle_timezone():
    vehicle = _vehicle()
    vehicle.timezone = dt.timezone(dt.timedelta(hours=9))

    _api()._update_vehicle_properties_ccs2(
        vehicle, {"Date": "20260908122241.000", "Offset": "9.000000"}
    )

    assert vehicle.last_updated_at == dt.datetime(
        2026, 9, 8, 21, 22, 41, tzinfo=dt.timezone(dt.timedelta(hours=9))
    )


def test_lock_uses_v1_ccs_token_without_pin():
    api = _api()
    token = _token(pin=None)
    vehicle = _vehicle()
    response = _response(
        {"metaInfo": {"retCode": "S"}, "data": {"svcSID": "lock-request"}}
    )

    with patch(
        "hyundai_kia_connect_api.HyundaiConnectApiKR.requests.post",
        return_value=response,
    ) as request:
        result = api.lock_action(token, vehicle, VEHICLE_LOCK_ACTION.LOCK)

    assert result == "lock-request"
    assert request.call_args.args[0].endswith(
        "/api/v1/apps/spa/tmc_new/ccsp/doorlock.do"
    )
    assert request.call_args.kwargs["headers"]["Authorization"] == "Bearer ccs-token"
    assert request.call_args.kwargs["json"]["ServiceNo"] == "F66"


def test_lock_accepts_success_response_with_service_number_only():
    api = _api()
    response = _response({"RetCode": "S", "ServiceNo": "F66", "svcTime": 0})

    with patch(
        "hyundai_kia_connect_api.HyundaiConnectApiKR.requests.post",
        return_value=response,
    ):
        result = api.lock_action(_token(pin=None), _vehicle(), VEHICLE_LOCK_ACTION.LOCK)

    assert result == "F66"


def test_unlock_exchanges_pin_for_control_token():
    api = _api()
    token = _token()
    vehicle = _vehicle()
    pin_response = _response({"controlTokenInfo": {"controlToken": "control-token"}})
    unlock_response = _response(
        {"metaInfo": {"retCode": "S"}, "data": {"SID": "unlock-request"}}
    )

    with patch(
        "hyundai_kia_connect_api.HyundaiConnectApiKR.requests.post",
        side_effect=[pin_response, unlock_response],
    ) as request:
        result = api.lock_action(token, vehicle, VEHICLE_LOCK_ACTION.UNLOCK)

    assert result == "unlock-request"
    assert request.call_args_list[0].args[0].endswith("/domain/api/v1/auth/pin")
    assert request.call_args_list[0].kwargs["json"] == {"pin": "1234"}
    assert (
        request.call_args_list[1]
        .args[0]
        .endswith("/api/v2/apps/spa/tmc_new/ccsp/pin/doorunlock.do")
    )
    assert request.call_args_list[1].kwargs["headers"]["Authorization"] == (
        "Bearer control-token"
    )
    assert request.call_args_list[1].kwargs["json"]["ServiceNo"] == "F67"


def test_unlock_without_pin_fails_before_remote_request():
    api = _api()

    with pytest.raises(PINMissingError, match="PIN is required"):
        api.lock_action(_token(pin=None), _vehicle(), VEHICLE_LOCK_ACTION.UNLOCK)


def test_gen2_ev_climate_start_sends_seats_and_steering_wheel():
    api = _api()
    token = _token()
    token.control_token = "control-token"
    token.control_token_expiry = float("inf")
    vehicle = _vehicle()
    vehicle.engine_type = ENGINE_TYPES.EV
    vehicle.remote_control_generation = "GEN2"
    vehicle.supports_remote_start = True
    vehicle.hvac_temperature_type = 1
    vehicle.steering_wheel_heater_option = 2
    vehicle.supports_steering_wheel_heater = True
    vehicle.front_left_seat_climate_capability = 6
    vehicle.front_right_seat_climate_capability = 2
    response = _response(
        {"metaInfo": {"retCode": "S"}, "data": {"svcSID": "climate-request"}}
    )
    options = ClimateRequestOptions(
        set_temp=21.5,
        duration=15,
        defrost=True,
        heating=2,
        front_left_seat=4,
        front_right_seat=8,
        steering_wheel=2,
    )

    with patch(
        "hyundai_kia_connect_api.HyundaiConnectApiKR.requests.post",
        return_value=response,
    ) as request:
        result = api.start_climate(token, vehicle, options)

    assert result == "climate-request"
    assert request.call_args.args[0].endswith(
        "/api/v2/apps/spa/tmc_new/ccsp/pin/remoteclimate_ccs2.do"
    )
    assert request.call_args.kwargs["headers"]["Authorization"] == (
        "Bearer control-token"
    )
    assert request.call_args.kwargs["json"] == {
        "CCID": "customer-id_BLU",
        "carID": "car-id",
        "CMD": "1",
        "ServiceNo": "E28",
        "airTemp": "21.5",
        "defrost": True,
        "hvacTempType": 1,
        "seatHeaterVentInfo": [{"drvSeatHeatState": 4, "astSeatHeatState": 8}],
        "sideRearMirrorHeating": 1,
        "wheelHeating": 2,
        "ignitionDuration": "15",
    }


def test_gen2_engine_start_uses_engine_endpoint_and_service_number():
    api = _api()
    token = _token(pin=None)
    vehicle = _vehicle()
    vehicle.engine_type = ENGINE_TYPES.HEV
    vehicle.remote_control_generation = "GEN2"
    vehicle.supports_remote_start = True
    vehicle.hvac_temperature_type = 1
    vehicle.steering_wheel_heater_option = 1
    vehicle.supports_steering_wheel_heater = True
    response = _response(
        {"metaInfo": {"retCode": "S"}, "data": {"SID": "engine-request"}}
    )
    options = ClimateRequestOptions(
        set_temp=22,
        climate=True,
        defrost=False,
        heating=3,
        front_left_seat=8,
        steering_wheel=1,
    )

    with patch(
        "hyundai_kia_connect_api.HyundaiConnectApiKR.requests.post",
        return_value=response,
    ) as request:
        result = api.start_climate(token, vehicle, options)

    assert result == "engine-request"
    assert request.call_args.args[0].endswith(
        "/api/v1/apps/spa/tmc_new/ccsp/engine_ccs2.do"
    )
    assert request.call_args.kwargs["json"] == {
        "CCID": "customer-id_BLU",
        "carID": "car-id",
        "CMD": "1",
        "ServiceNo": "F52",
        "AirCon": "1",
        "defrost": "2",
        "Remain": "2",
        "temp": "22.0",
        "hvacTempType": 1,
        "seatHeaterVentInfo": [{"drvSeatHeatState": 8}],
        "sideRearMirrorHeating": 0,
        "wheelHeating": 1,
    }


def test_gen2_engine_start_turns_omitted_supported_seats_off():
    api = _api()
    token = _token(pin=None)
    vehicle = _vehicle()
    vehicle.engine_type = ENGINE_TYPES.ICE
    vehicle.remote_control_generation = "GEN2"
    vehicle.supports_remote_start = True
    vehicle.front_left_seat_climate_capability = 6
    vehicle.front_right_seat_climate_capability = 2
    response = _response(
        {"metaInfo": {"retCode": "S"}, "data": {"SID": "engine-request"}}
    )

    with patch(
        "hyundai_kia_connect_api.HyundaiConnectApiKR.requests.post",
        return_value=response,
    ) as request:
        result = api.start_climate(
            token,
            vehicle,
            ClimateRequestOptions(front_right_seat=6),
        )

    assert result == "engine-request"
    assert request.call_args.kwargs["json"]["seatHeaterVentInfo"] == [
        {"drvSeatHeatState": 2, "astSeatHeatState": 6}
    ]


def test_gen2_ev_climate_stop_sends_minimal_stop_request():
    api = _api()
    token = _token(pin=None)
    vehicle = _vehicle()
    vehicle.engine_type = ENGINE_TYPES.EV
    vehicle.remote_control_generation = "GEN2"
    response = _response(
        {"metaInfo": {"retCode": "S"}, "data": {"svcSID": "stop-request"}}
    )

    with patch(
        "hyundai_kia_connect_api.HyundaiConnectApiKR.requests.post",
        return_value=response,
    ) as request:
        result = api.stop_climate(token, vehicle)

    assert result == "stop-request"
    assert request.call_args.args[0].endswith(
        "/api/v1/apps/spa/tmc_new/ccsp/remoteclimate_ccs2.do"
    )
    assert request.call_args.kwargs["json"] == {
        "CCID": "customer-id_BLU",
        "carID": "car-id",
        "CMD": "2",
        "ServiceNo": "E28",
    }


def test_gen2_engine_stop_matches_domestic_app_payload():
    api = _api()
    token = _token()
    token.control_token = "control-token"
    token.control_token_expiry = float("inf")
    vehicle = _vehicle()
    vehicle.engine_type = ENGINE_TYPES.ICE
    vehicle.remote_control_generation = "GEN2"
    vehicle.front_left_seat_climate_capability = 6
    vehicle.front_right_seat_climate_capability = 6
    vehicle.rear_left_seat_climate_capability = 1
    vehicle.rear_right_seat_climate_capability = 7
    response = _response({"RetCode": "S", "ServiceNo": "F52", "svcTime": 0})

    with patch(
        "hyundai_kia_connect_api.HyundaiConnectApiKR.requests.post",
        return_value=response,
    ) as request:
        result = api.stop_climate(token, vehicle)

    assert result == "F52"
    assert request.call_args.args[0].endswith(
        "/api/v2/apps/spa/tmc_new/ccsp/pin/engine_ccs2.do"
    )
    assert request.call_args.kwargs["json"] == {
        "CCID": "customer-id_BLU",
        "carID": "car-id",
        "CMD": "2",
        "ServiceNo": "F52",
        "Remain": "0",
        "seatHeaterVentInfo": [
            {
                "drvSeatHeatState": 2,
                "astSeatHeatState": 2,
                "rlSeatHeatState": 2,
            }
        ],
    }


@pytest.mark.parametrize(
    ("capability", "allowed"),
    [
        (1, {2, 6, 7}),
        (2, {2, 6, 7, 8}),
        (3, {2, 3, 4}),
        (4, {2, 3, 4, 5}),
        (5, {2, 3, 4, 6, 7}),
        (6, {2, 3, 4, 5, 6, 7, 8}),
        (8, {2, 8}),
        (9, {2, 5, 8}),
    ],
)
def test_seat_state_map_matches_myhyundai_model(capability, allowed):
    assert _api().SEAT_CLIMATE_STATES[capability] == allowed


def test_remote_start_rejects_ineligible_vehicle_before_control_request():
    api = _api()
    vehicle = _vehicle()
    api.get_vehicle_capabilities = MagicMock(
        side_effect=lambda _token, value: (
            setattr(value, "remote_control_generation", "GEN2"),
            setattr(value, "supports_remote_start", False),
        )
    )
    api._remote_control_post = MagicMock()

    with pytest.raises(APIError, match="not eligible"):
        api.start_climate(_token(), vehicle, ClimateRequestOptions())

    api._remote_control_post.assert_not_called()


def test_remote_start_rejects_unsupported_passenger_ventilation():
    api = _api()
    vehicle = _vehicle()
    vehicle.remote_control_generation = "GEN2"
    vehicle.supports_remote_start = True
    vehicle.front_right_seat_climate_capability = 2

    with pytest.raises(APIError, match="front_right_seat state 5"):
        api.start_climate(_token(), vehicle, ClimateRequestOptions(front_right_seat=5))


def test_vehicle_manager_completes_browser_login_and_initializes_vehicles():
    manager = VehicleManager(10, 2, "", "", "1234", language="ko")
    manager.api = MagicMock()
    manager.api.get_authorization_url.return_value = "https://login.example"
    manager.api.login_with_redirect_url.return_value = _token()
    manager.api.get_vehicles.return_value = []

    assert manager.get_authorization_url() == "https://login.example"
    assert manager.login_with_redirect_url("https://oneapp.hyundai.com/redirect")

    manager.api.login_with_redirect_url.assert_called_once_with(
        "https://oneapp.hyundai.com/redirect", pin="1234"
    )
    manager.api.get_vehicles.assert_called_once_with(manager.token)


def test_connected_car_id_survives_token_serialization():
    token = Token.from_dict(_token().to_dict())

    assert token.cc_id == "customer-id"


def test_domestic_error_reads_top_level_return_code():
    api = _api()
    response = _response(
        {"RetCode": "F", "resCode": "EEEE", "ServiceNo": "F39"},
        status_code=500,
    )

    with (
        patch(
            "hyundai_kia_connect_api.HyundaiConnectApiKR.requests.post",
            return_value=response,
        ),
        pytest.raises(APIError, match="EEEE"),
    ):
        api._domestic_post(_token(), "status", {})
