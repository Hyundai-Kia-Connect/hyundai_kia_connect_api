"""Genesis/Bluelink AU CCS2 controls: profile flags, frunk, lamps, windows,
pet care, battery conditioning and the async force-refresh fix.

Endpoint paths and bodies come from the Genesis Connected Services AU app
(com.genesis.aus 1.0.15, hmns.global RemoteViewModel / RemoteRepository) and
were cross-checked against live GCS responses for a 2025 GV70 (ICE, CCS2).
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from hyundai_kia_connect_api.ApiImpl import ClimateRequestOptions, WindowRequestOptions
from hyundai_kia_connect_api.const import ENGINE_TYPES, WINDOW_STATE
from hyundai_kia_connect_api.exceptions import APIError, UnsupportedControlError
from hyundai_kia_connect_api.KiaUvoApiAU import KiaUvoApiAU
from hyundai_kia_connect_api.Vehicle import Vehicle
from hyundai_kia_connect_api.VehicleManager import VehicleManager
from tests.fixture_helpers import load_fixture

OK = {"retCode": "S", "resCode": "0000", "msgId": "msg-1"}

# Redacted copy of the live GV70 /profile vinInfo[0] entry.
GV70_PROFILE = {
    "basic": {
        "vin": "KMTMC81CSSU000000",
        "modelName": "GV70 21/24",
        "modelYear": "2025",
        "modelType": "GN",
        "brand": "G",
        "protocolType": 1,
        "ccuCCS2ProtocolSupport": 1,
    },
    "option": {
        "airControlType": "1",
        "drvSeatLoc": "R",
        "remoteControl": "1",
        "heating1": "1",
        "strgWhlHeatOption": 2,
        "hvacTempType": "1",
        "remoteControlWaitingTime": 336,
        "windowSafetyOption2": 3,
        "svmOptionInfo": 1,
        "seatHeaterVent": {
            "drvSeatHeat": 6,
            "astSeatHeat": 6,
            "rlSeatHeat": 2,
            "rrSeatHeat": 2,
        },
        "cmsOption": 0,
        "sunRoofOption": 1,
        "airPurifierOption": 0,
        "ignCtrlOption": 1,
        "fineDustOption": 1,
    },
    "serviceOption": {
        "scheduleLinkService": 1,
        "valetServiceOption": 1,
        "remoteValetActOption": 1,
    },
}


@pytest.fixture
def au_api() -> KiaUvoApiAU:
    api = KiaUvoApiAU.__new__(KiaUvoApiAU)
    api.SPA_API_URL = "https://test.invalid/api/v1/spa/"
    api.SPA_API_URL_V2 = "https://test.invalid/api/v2/spa/"
    api.session = MagicMock()
    api._get_authenticated_headers = MagicMock(return_value={"Authorization": "a"})
    api._get_control_headers = MagicMock(return_value={"Authorization": "ctl"})
    api._update_vehicle_properties_ccs2 = MagicMock()
    api._get_location = MagicMock(return_value=None)
    return api


@pytest.fixture
def token():
    return SimpleNamespace(access_token="t", device_id="dev-1", pin="1234")


@pytest.fixture
def ccs2_vehicle() -> Vehicle:
    v = Vehicle()
    v.id = "vid-1"
    v.ccu_ccs2_protocol_support = 1
    v.engine_type = ENGINE_TYPES.ICE
    return v


@pytest.fixture
def legacy_vehicle() -> Vehicle:
    v = Vehicle()
    v.id = "vid-2"
    v.ccu_ccs2_protocol_support = 0
    v.engine_type = ENGINE_TYPES.ICE
    return v


def _post_ok(api):
    resp = MagicMock()
    resp.json.return_value = OK
    api.session.post.return_value = resp
    return resp


def _post_call(api):
    args, kwargs = api.session.post.call_args
    return args[0], kwargs["json"], kwargs["headers"]


# ---------------------------------------------------------------- profile


def test_apply_profile_sets_flags_for_ice_gv70(ccs2_vehicle):
    KiaUvoApiAU._apply_vehicle_profile(ccs2_vehicle, GV70_PROFILE)
    v = ccs2_vehicle
    assert v.year == 2025
    assert v.vehicle_options["drvSeatLoc"] == "R"
    assert v.service_options["scheduleLinkService"] == 1
    # Not advertised on this car
    assert v.supports_frunk is False
    # lamp-off is offered on any remote-control capable car (status-gated)
    assert v.supports_lamp_control is True
    assert v.supports_rear_window_curtain is False
    assert v.supports_pet_care is False
    assert v.supports_battery_conditioning is False
    assert v.supports_air_purifier is False
    assert v.supports_third_row_seat_climate is False
    # Advertised
    assert v.supports_sunroof is True
    assert v.supports_window_control is True


def test_apply_profile_ev_flags():
    v = Vehicle()
    profile = {
        "option": {
            "remoteControl": "1",
            "frunkOption": 1,
            "batteryPreconditioningOption": 1,
            "rearWindowCurtainOption": 1,
            "airPurifierOption": 1,
            "seatHeaterVent": {"thirdlSeatHeatOption": 2, "thirdrSeatHeatOption": 0},
        },
        "serviceOption": {
            "petCareService": 1,
            "remoteLightControlOption": 1,
            "windowControlOption": 0,
        },
    }
    KiaUvoApiAU._apply_vehicle_profile(v, profile)
    assert v.supports_frunk is True
    assert v.supports_battery_conditioning is True
    assert v.supports_rear_window_curtain is True
    assert v.supports_air_purifier is True
    assert v.supports_pet_care is True
    assert v.supports_lamp_control is True
    assert v.supports_third_row_seat_climate is True
    # explicit serviceOption override wins
    assert v.supports_window_control is False


def test_apply_profile_without_window_keys_leaves_flag_unknown():
    v = Vehicle()
    KiaUvoApiAU._apply_vehicle_profile(v, {"option": {}, "serviceOption": {}})
    assert v.supports_window_control is None


def test_apply_profile_none_is_noop():
    v = Vehicle()
    KiaUvoApiAU._apply_vehicle_profile(v, None)
    assert v.vehicle_options is None
    assert v.supports_frunk is None


def test_get_vehicles_fetches_profile(au_api, token):
    base_vehicle = Vehicle(id="vid-1", ccu_ccs2_protocol_support=1)
    with patch(
        "hyundai_kia_connect_api.ApiImplType1.ApiImplType1.get_vehicles",
        return_value=[base_vehicle],
    ):
        resp = MagicMock()
        resp.json.return_value = {
            "retCode": "S",
            "resCode": "0000",
            "resMsg": {"vinInfo": [GV70_PROFILE]},
            "msgId": "m",
        }
        au_api.session.get.return_value = resp
        vehicles = au_api.get_vehicles(token)
    assert au_api.session.get.call_args[0][0].endswith("/vehicles/vid-1/profile")
    assert vehicles[0].supports_sunroof is True
    assert vehicles[0].year == 2025


def test_get_vehicles_survives_profile_failure(au_api, token):
    base_vehicle = Vehicle(id="vid-1", ccu_ccs2_protocol_support=1)
    with patch(
        "hyundai_kia_connect_api.ApiImplType1.ApiImplType1.get_vehicles",
        return_value=[base_vehicle],
    ):
        au_api.session.get.side_effect = RuntimeError("boom")
        vehicles = au_api.get_vehicles(token)
    assert vehicles[0].supports_frunk is None


def test_vehicle_manager_keeps_api_derived_flags():
    vm = VehicleManager.__new__(VehicleManager)
    vm.vehicles = {}
    vm.token = None
    v = Vehicle(id="vid-1")
    v.supports_window_control = False
    vm.api = SimpleNamespace(
        supports_window_control=True,
        supports_valet_mode=True,
        supports_svm=None,
        get_vehicles=lambda _token: [v],
    )
    vm.initialize_vehicles()
    assert vm.vehicles["vid-1"].supports_window_control is False
    assert vm.vehicles["vid-1"].supports_valet_mode is True


# ----------------------------------------------------------- force refresh


def _latest(last_update: str) -> dict:
    return {
        "retCode": "S",
        "resCode": "0000",
        "resMsg": {
            "lastUpdateTime": last_update,
            "state": {"Vehicle": {"Date": last_update + ".000"}},
        },
    }


def test_force_refresh_ccs2_polls_until_snapshot_advances(au_api, token, ccs2_vehicle):
    """baseline /latest -> wake (ack only) -> poll /latest until lastUpdateTime moves."""
    urls = []
    latest_responses = iter(
        [
            _latest("20261013044043"),
            _latest("20261013044043"),
            _latest("20261013044415"),
        ]
    )

    def mock_get(url, headers=None):
        urls.append(url.rsplit("/vid-1", 1)[1])
        resp = MagicMock()
        if url.endswith("/ccs2/carstatus"):
            resp.json.return_value = OK  # async ack, no resMsg (live GCS)
        else:
            resp.json.return_value = next(latest_responses)
        return resp

    sleeps = []
    au_api.CCS2_REFRESH_POLL_INTERVAL = 3
    with (
        patch.object(au_api.session, "get", side_effect=mock_get),
        patch("hyundai_kia_connect_api.KiaUvoApiAU.sleep", side_effect=sleeps.append),
    ):
        au_api._force_refresh_vehicle_state_ccs2(token, ccs2_vehicle)

    assert urls == [
        "/ccs2/carstatus/latest",  # baseline
        "/ccs2/carstatus",  # wake
        "/ccs2/carstatus/latest",  # unchanged
        "/ccs2/carstatus/latest",  # advanced -> stop
    ]
    assert sleeps == [3, 3]
    au_api._update_vehicle_properties_ccs2.assert_called_once_with(
        ccs2_vehicle, {"Date": "20261013044415.000"}
    )


def test_force_refresh_ccs2_times_out_and_applies_cached(au_api, token, ccs2_vehicle):
    def mock_get(url, headers=None):
        resp = MagicMock()
        resp.json.return_value = (
            OK if url.endswith("/ccs2/carstatus") else _latest("20261013044043")
        )
        return resp

    au_api.CCS2_REFRESH_TIMEOUT = 0  # deadline already passed after first poll
    sleeps = []
    with (
        patch.object(au_api.session, "get", side_effect=mock_get),
        patch("hyundai_kia_connect_api.KiaUvoApiAU.sleep", side_effect=sleeps.append),
    ):
        au_api._force_refresh_vehicle_state_ccs2(token, ccs2_vehicle)

    assert sleeps == [au_api.CCS2_REFRESH_POLL_INTERVAL]
    au_api._update_vehicle_properties_ccs2.assert_called_once_with(
        ccs2_vehicle, {"Date": "20261013044043.000"}
    )


def test_force_refresh_ccs2_wake_error_propagates(au_api, token, ccs2_vehicle):
    def mock_get(url, headers=None):
        resp = MagicMock()
        if url.endswith("/ccs2/carstatus"):
            resp.json.return_value = {"retCode": "F", "resCode": "5091", "resMsg": "x"}
        else:
            resp.json.return_value = _latest("20261013044043")
        return resp

    with (
        patch.object(au_api.session, "get", side_effect=mock_get),
        patch("hyundai_kia_connect_api.KiaUvoApiAU.sleep"),
        pytest.raises(APIError),
    ):
        au_api._force_refresh_vehicle_state_ccs2(token, ccs2_vehicle)
    au_api._update_vehicle_properties_ccs2.assert_not_called()


# ------------------------------------------------------------------ frunk


def test_open_frunk_ccs2_payload(au_api, token, ccs2_vehicle):
    ccs2_vehicle.supports_frunk = True
    _post_ok(au_api)
    assert au_api.open_frunk(token, ccs2_vehicle) == "msg-1"
    url, body, headers = _post_call(au_api)
    assert url.endswith("/api/v2/spa/vehicles/vid-1/ccs2/control/frunk")
    assert body == {"command": 1}
    assert headers == {"Authorization": "ctl"}


def test_open_frunk_rejects_unsupported(au_api, token, ccs2_vehicle, legacy_vehicle):
    ccs2_vehicle.supports_frunk = False
    with pytest.raises(UnsupportedControlError):
        au_api.open_frunk(token, ccs2_vehicle)
    with pytest.raises(UnsupportedControlError):
        au_api.open_frunk(token, legacy_vehicle)
    au_api.session.post.assert_not_called()


# ------------------------------------------------------------------ lamps


def test_set_lamp_state_ccs2(au_api, token, ccs2_vehicle):
    _post_ok(au_api)
    au_api.set_lamp_state(token, ccs2_vehicle, tail_lamp=False, hazard_lamp=False)
    url, body, _ = _post_call(au_api)
    assert url.endswith("/api/v2/spa/vehicles/vid-1/ccs2/control/lamp")
    assert body == {"tailLamp": 0, "hazardLamp": 0}


def test_set_lamp_state_ccs2_omits_unchanged_lamp(au_api, token, ccs2_vehicle):
    _post_ok(au_api)
    au_api.set_lamp_state(token, ccs2_vehicle, hazard_lamp=False)
    _, body, _ = _post_call(au_api)
    assert body == {"hazardLamp": 0}


def test_set_lamp_state_legacy_uses_hazard_key(au_api, token, legacy_vehicle):
    _post_ok(au_api)
    au_api.set_lamp_state(token, legacy_vehicle, tail_lamp=True, hazard_lamp=False)
    url, body, _ = _post_call(au_api)
    assert url.endswith("/api/v2/spa/vehicles/vid-2/control/lamp")
    assert body == {"tailLamp": 1, "hazard": 0}


def test_set_lamp_state_requires_a_lamp(au_api, token, ccs2_vehicle):
    with pytest.raises(ValueError):
        au_api.set_lamp_state(token, ccs2_vehicle)


def test_set_lamp_state_unsupported(au_api, token, ccs2_vehicle):
    ccs2_vehicle.supports_lamp_control = False
    with pytest.raises(UnsupportedControlError):
        au_api.set_lamp_state(token, ccs2_vehicle, tail_lamp=False)


# ---------------------------------------------------------------- windows


def test_set_windows_state_ccs2_rhd_mapping_and_curtains(au_api, token, ccs2_vehicle):
    _post_ok(au_api)
    options = WindowRequestOptions(
        front_left=WINDOW_STATE.OPEN,
        front_right=WINDOW_STATE.CLOSED,
        back_left=WINDOW_STATE.VENTILATION,
        back_right=WINDOW_STATE.CLOSED,
        rear_left_curtain=WINDOW_STATE.OPEN,
        rear_right_curtain=WINDOW_STATE.CLOSED,
    )
    au_api.set_windows_state(token, ccs2_vehicle, options)
    url, body, _ = _post_call(au_api)
    assert url.endswith("/api/v2/spa/vehicles/vid-1/ccs2/control/windowcurtain")
    # AU is RHD: driver window = physical front right
    assert body == {
        "drvSeatWindow": WINDOW_STATE.CLOSED,
        "psgSeatWindow": WINDOW_STATE.OPEN,
        "rlSeatWindow": WINDOW_STATE.VENTILATION,
        "rrSeatWindow": WINDOW_STATE.CLOSED,
        "drvSeatLoc": "R",
        "rlSeatWindowCurtain": WINDOW_STATE.OPEN,
        "rrSeatWindowCurtain": WINDOW_STATE.CLOSED,
    }


def test_set_windows_state_ccs2_without_curtains(au_api, token, ccs2_vehicle):
    _post_ok(au_api)
    options = WindowRequestOptions(
        front_left=WINDOW_STATE.CLOSED,
        front_right=WINDOW_STATE.CLOSED,
        back_left=WINDOW_STATE.CLOSED,
        back_right=WINDOW_STATE.CLOSED,
    )
    au_api.set_windows_state(token, ccs2_vehicle, options)
    _, body, _ = _post_call(au_api)
    assert "rlSeatWindowCurtain" not in body
    assert "rrSeatWindowCurtain" not in body
    assert set(body) == {
        "drvSeatWindow",
        "psgSeatWindow",
        "rlSeatWindow",
        "rrSeatWindow",
        "drvSeatLoc",
    }


def test_set_windows_state_legacy_keeps_position_body(au_api, token, legacy_vehicle):
    _post_ok(au_api)
    options = WindowRequestOptions(
        front_left=WINDOW_STATE.OPEN,
        front_right=WINDOW_STATE.OPEN,
        back_left=WINDOW_STATE.OPEN,
        back_right=WINDOW_STATE.OPEN,
        rear_left_curtain=WINDOW_STATE.CLOSED,
    )
    au_api.set_windows_state(token, legacy_vehicle, options)
    url, body, _ = _post_call(au_api)
    assert url.endswith("/api/v2/spa/vehicles/vid-2/control/windowcurtain")
    assert body == {
        "backLeft": WINDOW_STATE.OPEN,
        "backRight": WINDOW_STATE.OPEN,
        "frontLeft": WINDOW_STATE.OPEN,
        "frontRight": WINDOW_STATE.OPEN,
        "rearCurtainLeft": WINDOW_STATE.CLOSED,
    }


# --------------------------------------------------------------- pet care


def test_start_pet_care_payload(au_api, token, ccs2_vehicle):
    ccs2_vehicle.supports_pet_care = True
    _post_ok(au_api)
    au_api.start_pet_care(token, ccs2_vehicle, 22, "c")
    url, body, _ = _post_call(au_api)
    assert url.endswith("/api/v2/spa/vehicles/vid-1/ccs2/control/petcare")
    assert body == {"hvacTemp": "22.0", "tempUnit": "C"}


def test_start_pet_care_rejects_bad_unit_and_unsupported(au_api, token, ccs2_vehicle):
    with pytest.raises(ValueError):
        au_api.start_pet_care(token, ccs2_vehicle, 22, "K")
    ccs2_vehicle.supports_pet_care = False
    with pytest.raises(UnsupportedControlError):
        au_api.start_pet_care(token, ccs2_vehicle, 22)


# --------------------------------------------------- battery conditioning


def test_set_battery_conditioning_payloads(au_api, token, ccs2_vehicle):
    ccs2_vehicle.engine_type = ENGINE_TYPES.EV
    ccs2_vehicle.supports_battery_conditioning = True
    _post_ok(au_api)
    au_api.set_battery_conditioning(token, ccs2_vehicle, True)
    url, body, _ = _post_call(au_api)
    assert url.endswith("/api/v2/spa/vehicles/vid-1/ccs2/control/batteryconditioning")
    assert body == {"command": 1}
    au_api.set_battery_conditioning(token, ccs2_vehicle, False)
    _, body, _ = _post_call(au_api)
    assert body == {"command": 0}


def test_set_battery_conditioning_rejects_ice(au_api, token, ccs2_vehicle):
    with pytest.raises(UnsupportedControlError):
        au_api.set_battery_conditioning(token, ccs2_vehicle, True)
    au_api.session.post.assert_not_called()


# ---------------------------------------------------------------- climate


def test_start_climate_ccs2_extras_only_when_requested(au_api, token, ccs2_vehicle):
    au_api.temperature_range = KiaUvoApiAU.temperature_range
    _post_ok(au_api)
    au_api.start_climate(token, ccs2_vehicle, ClimateRequestOptions(set_temp=21))
    _, body, _ = _post_call(au_api)
    assert "airPurifierControl" not in body
    assert "thrlSeatClimateState" not in body["seatClimateInfo"]

    au_api.start_climate(
        token,
        ccs2_vehicle,
        ClimateRequestOptions(
            set_temp=21,
            defrost=True,
            air_purifier=1,
            third_row_left_seat=6,
            third_row_right_seat=0,
        ),
    )
    _, body, _ = _post_call(au_api)
    assert body["airPurifierControl"] == 1
    # the apps force the front defog off while the purifier runs
    assert body["windshieldFrontDefogState"] is False
    assert body["seatClimateInfo"]["thrlSeatClimateState"] == 6
    assert body["seatClimateInfo"]["thrrSeatClimateState"] == 0


# ------------------------------------------------------------ lamp alerts


def test_ccs2_lamp_alert_status():
    api = KiaUvoApiAU.__new__(KiaUvoApiAU)
    api.data_timezone = KiaUvoApiAU.data_timezone
    api.temperature_range = KiaUvoApiAU.temperature_range
    state = load_fixture("eu_kia_ev9_2024_ccs2.json")
    state.setdefault("Body", {}).setdefault("Lights", {})
    state["Body"]["Lights"]["TailLamp"] = {"Alert": 1}
    state["Body"]["Lights"]["Hazard"] = {"Alert": 0}
    v = Vehicle()
    api._update_vehicle_properties_ccs2(v, state)
    assert v.tail_lamp_alert_is_on is True
    assert v.hazard_lamp_alert_is_on is False

    # Absent on the wire -> None so no entity is created for such cars
    state["Body"]["Lights"].pop("TailLamp")
    state["Body"]["Lights"].pop("Hazard")
    v2 = Vehicle()
    api._update_vehicle_properties_ccs2(v2, state)
    assert v2.tail_lamp_alert_is_on is None
    assert v2.hazard_lamp_alert_is_on is None
