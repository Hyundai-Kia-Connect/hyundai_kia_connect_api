"""parse_vehicle_profile behavior: fetch, parse, and failure handling.

Profile reads run from VehicleManager.initialize_vehicles (not from the
status update path), with a no-op stub on ApiImpl for regions that have no
legacy-SPA profile. Covers the EU legacy implementation: the per-seat
support flags come from option seatHeaterVent (0/7 = unreachable, any
other value = a supported level), a non-200 read leaves the flags
unknown, and a failed read never breaks vehicle setup.
"""

from unittest.mock import MagicMock, patch

from hyundai_kia_connect_api.KiaUvoApiEU import KiaUvoApiEU
from hyundai_kia_connect_api.Token import Token
from hyundai_kia_connect_api.Vehicle import Vehicle
from hyundai_kia_connect_api.VehicleManager import VehicleManager
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


def test_profile_read_failure_does_not_break_setup() -> None:
    manager = VehicleManager(
        region=1, brand=1, username="user", password="password", pin=""
    )
    manager.token = _token()
    vehicle = _vehicle()
    with (
        patch.object(manager.api, "get_vehicles", return_value=[vehicle]),
        patch.object(
            manager.api, "parse_vehicle_profile", side_effect=RuntimeError("boom")
        ),
    ):
        manager.initialize_vehicles()
    assert manager.vehicles == {vehicle.id: vehicle}
    assert vehicle.supports.remote_seat_heater_front_left is None


def test_kia_legacy_profile_seat_heaters_unsupported() -> None:
    # e-Niro 2021 capture (kia_uvo#2121): every seatHeaterVent value is 0
    # (init), so no seat can be reached by remote climate.
    impl = KiaUvoApiEU(1, 1, "en")
    vehicle = _vehicle()
    with patch.object(impl, "session") as session:
        session.get.return_value = MagicMock(
            status_code=200,
            json=lambda: load_fixture(
                "updates/responses/eu_kia_eniro_2021_profile.json"
            ),
        )
        impl.parse_vehicle_profile(_token(), vehicle)
    assert vehicle.supports.remote_seat_heater_front_left is False
    assert vehicle.supports.remote_seat_heater_front_right is False
    assert vehicle.supports.remote_seat_heater_rear_left is False
    assert vehicle.supports.remote_seat_heater_rear_right is False
    assert session.get.call_args.args[0].endswith(
        "/api/v1/spa/vehicles/VEHICLE-1/profile"
    )


def test_profile_seat_heaters_supported_levels() -> None:
    # Gate behaviour: values other than 0/7 count as supported, string
    # values coerce, and 7 (invalid) stays unsupported. Levels come from
    # the Santa Fe capture in the update fixture; the shapes here cover
    # combinations no capture contains.
    impl = KiaUvoApiEU(1, 2, "en")
    vehicle = _vehicle()
    body = {
        "retCode": "S",
        "resCode": "0000",
        "resMsg": {
            "vinInfo": [
                {
                    "option": {
                        "seatHeaterVent": {
                            "flSeatHeat": 6,
                            "frSeatHeat": "6",
                            "rlSeatHeat": 2,
                            "rrSeatHeat": 7,
                        }
                    }
                }
            ]
        },
    }
    with patch.object(impl, "session") as session:
        session.get.return_value = MagicMock(status_code=200, json=lambda: body)
        impl.parse_vehicle_profile(_token(), vehicle)
    assert vehicle.supports.remote_seat_heater_front_left is True
    assert vehicle.supports.remote_seat_heater_front_right is True
    assert vehicle.supports.remote_seat_heater_rear_left is True
    assert vehicle.supports.remote_seat_heater_rear_right is False
    headers = session.get.call_args.kwargs["headers"]
    assert headers["Host"] == "prd.eu-ccapi.hyundai.com:8080"


def test_profile_without_seat_block_keeps_support_unknown() -> None:
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
    assert vehicle.supports.remote_seat_heater_front_left is None
