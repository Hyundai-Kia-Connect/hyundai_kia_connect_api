"""parse_vehicle_profile behavior: fetch, parse, and failure handling.

Profile reads run from VehicleManager.initialize_vehicles (not from the
status update path), with a no-op stub on ApiImpl for regions that have no
legacy-SPA profile. Covers the two EU implementations: the flag comes from
option clmtCtrlOption == 1 (absent = unsupported, as the app gates it), a
non-200 read leaves the flag unknown, and the GSPA read registers a
legacy-SPA device id lazily.
"""

from unittest.mock import MagicMock, patch

from hyundai_kia_connect_api.HyundaiCciApiEU import HyundaiCciApiEU
from hyundai_kia_connect_api.KiaUvoApiEU import KiaUvoApiEU
from hyundai_kia_connect_api.Token import Token
from hyundai_kia_connect_api.Vehicle import Vehicle
from tests.fixture_helpers import load_fixture


def _token() -> Token:
    return Token(
        username="user",
        password="password",
        pin="1234",
        access_token="Bearer access",
        refresh_token="refresh",
        device_id="0123456789abcdef0123456789abcdef",
    )


def _vehicle() -> Vehicle:
    vehicle = Vehicle(id="VEHICLE-1")
    vehicle.ccu_ccs2_protocol_support = 1
    return vehicle


def test_gspa_profile_parse_sets_flag_false_without_option() -> None:
    # Santa Fe HEV capture: the option block carries no clmtCtrlOption key.
    impl = HyundaiCciApiEU(9, 2, "en")
    impl._legacy_spa_device_id = "legacy-device-1"
    vehicle = _vehicle()
    with patch.object(impl, "session") as session:
        session.get.return_value = MagicMock(
            status_code=200,
            json=lambda: load_fixture(
                "updates/responses/eu_hyundai_gspa_santa_fe_2026_hev_profile.json"
            ),
        )
        impl.parse_vehicle_profile(_token(), vehicle)
    assert vehicle.supports.climate_duration_setting is False
    headers = session.get.call_args.kwargs["headers"]
    assert headers["Host"] == "prd.eu-ccapi.hyundai.com:8080"
    assert session.get.call_args.args[0].endswith(
        "/api/v1/spa/vehicles/VEHICLE-1/profile"
    )


def test_gspa_profile_registers_legacy_device_lazily() -> None:
    impl = HyundaiCciApiEU(9, 2, "en")
    vehicle = _vehicle()
    with patch.object(impl, "session") as session:
        register = MagicMock(
            status_code=200, json=lambda: {"resMsg": {"deviceId": "dev-1"}}
        )
        profile = MagicMock(
            status_code=200,
            json=lambda: load_fixture(
                "updates/responses/eu_hyundai_gspa_santa_fe_2026_hev_profile.json"
            ),
        )
        session.post.return_value = register
        session.get.return_value = profile
        impl.parse_vehicle_profile(_token(), vehicle)
    assert impl._legacy_spa_device_id == "dev-1"
    assert session.post.call_args.args[0].endswith("/api/v1/spa/notifications/register")


def test_gspa_profile_error_keeps_flag_unknown() -> None:
    impl = HyundaiCciApiEU(9, 2, "en")
    impl._legacy_spa_device_id = "legacy-device-1"
    vehicle = _vehicle()
    with patch.object(impl, "session") as session:
        session.get.return_value = MagicMock(status_code=503, json=dict)
        impl.parse_vehicle_profile(_token(), vehicle)
    assert vehicle.supports.climate_duration_setting is None


def test_type1_profile_parse_sets_flag_from_option() -> None:
    impl = KiaUvoApiEU(1, 2, "en")
    vehicle = _vehicle()
    body = {
        "retCode": "S",
        "resCode": "0000",
        "resMsg": {"vinInfo": [{"option": {"clmtCtrlOption": 1}}]},
    }
    with patch.object(impl, "session") as session:
        session.get.return_value = MagicMock(status_code=200, json=lambda: body)
        impl.parse_vehicle_profile(_token(), vehicle)
    assert vehicle.supports.climate_duration_setting is True
    assert session.get.call_args.args[0].endswith(
        "/api/v1/spa/vehicles/VEHICLE-1/profile"
    )
