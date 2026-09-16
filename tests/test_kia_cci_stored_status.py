"""Tests for KiaCciApiEU.update_vehicle_with_cached_state — the shared
CCS2 property parser applied to a live Kia EV6 GSPA stored-status
fixture (zero network). Fixture leaf values are synthetic (replaced by
the volunteer before sharing); structure and key names are real."""

from unittest.mock import patch

import pytest

from hyundai_kia_connect_api.KiaCciApiEU import KiaCciApiEU
from hyundai_kia_connect_api.Token import Token
from hyundai_kia_connect_api.Vehicle import Vehicle
from tests.fixture_helpers import load_fixture

FIXTURE = "eu_kia_gspa_ev6_2024_stored_status.json"


@pytest.fixture
def api() -> KiaCciApiEU:
    return KiaCciApiEU.__new__(KiaCciApiEU)


@pytest.fixture
def vehicle() -> Vehicle:
    return Vehicle()


def test_ccs2_parser_on_kia_stored_status(api: KiaCciApiEU, vehicle: Vehicle) -> None:
    """The shared CCS2 parser handles the Kia EV6 state tree."""
    data = load_fixture(FIXTURE)
    api._update_vehicle_properties_ccs2(vehicle, data)

    assert vehicle.ev_battery_percentage == 70.0
    assert vehicle.car_battery_percentage == 70
    # Kia sends DrivingReady as 0/1, Hyundai as a bool — both falsy.
    assert not vehicle.engine_is_running
    assert vehicle.front_left_door_is_locked is True
    assert vehicle.is_locked is True
    # HVAC temperature is 'OFF' → no temperature set.
    assert vehicle.air_temperature is None
    # Hyundai-specific sections absent from the Kia tree stay None
    # (graceful degradation, not masking): OutsideTemperature is not in
    # the EV6 payload.
    assert vehicle.outside_temperature is None


def test_update_vehicle_with_cached_state(api: KiaCciApiEU, vehicle: Vehicle) -> None:
    """update_vehicle_with_cached_state feeds stored-status state.Vehicle
    to the shared parser and does not call the Hyundai-only driving
    parsers (they are not part of the Kia API surface)."""
    state_vehicle = {
        k: v for k, v in load_fixture(FIXTURE).items() if k != "_fixture_meta"
    }
    stored = {"serviceNo": "RVS-K 1789000000000", "state": {"Vehicle": state_vehicle}}
    with patch.object(
        KiaCciApiEU, "get_stored_status", return_value=stored
    ) as get_status:
        token = Token()
        token.access_token = "ccs-token"
        api.update_vehicle_with_cached_state(token, vehicle)
    get_status.assert_called_once()

    assert vehicle.ev_battery_percentage == 70.0
    assert vehicle.car_battery_percentage == 70


def test_update_vehicle_with_cached_state_no_ccs_token(
    api: KiaCciApiEU, vehicle: Vehicle
) -> None:
    """A token without CCS credentials raises APIError before any call."""
    from hyundai_kia_connect_api.exceptions import APIError

    token = Token()
    with (
        patch.object(KiaCciApiEU, "get_stored_status") as get_status,
        pytest.raises(APIError),
    ):
        api.update_vehicle_with_cached_state(token, vehicle)
    get_status.assert_not_called()


def test_update_vehicle_with_cached_state_no_data(
    api: KiaCciApiEU, vehicle: Vehicle
) -> None:
    """A None stored-status response raises APIError."""
    from hyundai_kia_connect_api.exceptions import APIError

    with patch.object(KiaCciApiEU, "get_stored_status", return_value=None):
        token = Token()
        token.access_token = "ccs-token"
        with pytest.raises(APIError):
            api.update_vehicle_with_cached_state(token, vehicle)


def test_kia_api_has_no_hyundai_driving_parsers() -> None:
    """Driving info/history parsers stay Hyundai-specific (D7)."""
    assert not hasattr(KiaCciApiEU, "_get_driving_info")
    assert not hasattr(KiaCciApiEU, "_get_driving_history")
    assert not hasattr(KiaCciApiEU, "_parse_breakdowns")
