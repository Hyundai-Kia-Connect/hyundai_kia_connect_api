"""Japan observations are applied from local MQTT data without vehicle requests."""

import copy
import datetime as dt
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from hyundai_kia_connect_api.HyundaiBlueLinkApiJP import HyundaiBlueLinkApiJP
from hyundai_kia_connect_api.Token import Token
from hyundai_kia_connect_api.Vehicle import Vehicle


def state(date="20261103020000.000", lock=0):
    return {
        "Date": date,
        "Drivetrain": {"FuelSystem": {"DTE": {"Total": 300, "Unit": 1}}},
        "Cabin": {
            "Door": {
                "Row1": {"Driver": {"Lock": lock}, "Passenger": {"Lock": 0}},
                "Row2": {"Left": {"Lock": 0}, "Right": {"Lock": 0}},
            }
        },
    }


def delta(date="20261103020020.000", lock=1):
    return {"Date": date, "Cabin": {"Door": {"Row1": {"Driver": {"Lock": lock}}}}}


def setup():
    api = HyundaiBlueLinkApiJP(10, 2, "en")
    api.session = Mock()
    api._request = Mock(side_effect=AssertionError("passive reads must not request"))
    api._vehicle_profiles["synthetic"] = {"option": {"drvSeatLoc": "R"}}
    vehicle = Vehicle(id="synthetic", ccu_ccs2_protocol_support=1)
    api._update_vehicle_properties_ccs2(vehicle, state())
    helper = SimpleNamespace(latest_state=None, diagnostics={})
    api._mqtt_sessions[vehicle.id] = ((), helper)
    return api, vehicle, helper


def test_delayed_unlock_is_applied_without_network_or_optimistic_change():
    api, vehicle, helper = setup()
    assert vehicle.is_locked is True
    assert not api.apply_cached_mqtt_state(vehicle)
    assert vehicle.is_locked is True
    helper.latest_state = delta()
    assert api.apply_cached_mqtt_state(vehicle)
    assert vehicle.is_locked is False
    assert vehicle.front_right_door_is_locked is False
    assert vehicle.front_left_door_is_locked is True
    assert vehicle.last_updated_at == dt.datetime(2026, 11, 3, 2, 0, 20, tzinfo=dt.UTC)
    api._request.assert_not_called()
    api.session.request.assert_not_called()
    assert api.get_local_diagnostics()["passive_state"]["applied"] == 1


def test_queued_unlock_then_unrelated_delta_cannot_overwrite_the_unlock():
    api, vehicle, helper = setup()
    helper.latest_state = {"Date": "20261103020021.000", "DrivingReady": False}
    helper.drain_state_updates = Mock(
        return_value=[
            helper.latest_state,
            delta(),  # sorted by source date, not callback order
        ]
    )
    assert api.apply_cached_mqtt_state(vehicle)
    assert vehicle.is_locked is False
    assert vehicle.engine_is_running is False
    assert vehicle.last_updated_at == dt.datetime(2026, 11, 3, 2, 0, 21, tzinfo=dt.UTC)
    helper.drain_state_updates.assert_called_once()
    api._request.assert_not_called()


def test_empty_receive_queue_does_not_reapply_latest_snapshot():
    api, vehicle, helper = setup()
    helper.latest_state = delta()
    helper.drain_state_updates = Mock(return_value=[])
    before = copy.deepcopy(vehicle.__dict__)
    assert not api.apply_cached_mqtt_state(vehicle)
    assert vehicle.__dict__ == before


@pytest.mark.parametrize("date", ["20261103015959.999", "20261103020000.000"])
def test_stale_or_duplicate_observation_does_not_change_state(date):
    api, vehicle, helper = setup()
    helper.latest_state = delta(date)
    before = copy.deepcopy(vehicle.__dict__)
    assert not api.apply_cached_mqtt_state(vehicle)
    assert vehicle.__dict__ == before


def test_fractional_same_second_update_is_not_discarded():
    api, vehicle, helper = setup()
    vehicle.data["Date"] = "20261103020000.100"
    helper.latest_state = delta("20261103020000.200")
    assert api.apply_cached_mqtt_state(vehicle)
    assert vehicle.is_locked is False
    assert vehicle.last_updated_at.microsecond == 200000


def test_unrelated_partial_state_preserves_missing_door_and_optional_data():
    api, vehicle, helper = setup()
    vehicle.data.pop("Cabin")
    vehicle.ev_charge_limits_ac = 80
    vehicle.car_battery_percentage = 63
    vehicle.engine_is_running = True
    helper.latest_state = {"Date": "20261103020020.000", "DrivingReady": False}
    assert api.apply_cached_mqtt_state(vehicle)
    assert vehicle.is_locked is True
    assert vehicle.ev_charge_limits_ac == 80
    assert vehicle.car_battery_percentage == 63
    assert vehicle.engine_is_running is False


@pytest.mark.parametrize(
    "observation",
    [
        {},
        {"Cabin": {}},
        {"Date": "invalid", "DrivingReady": False},
        {"Date": "20261103020020.000"},
        {"Date": "20261103020020.000", "Cabin": False},
        delta(lock=True),
        delta(lock="1"),
        delta(lock={"value": 1}),
        {"Date": "20261103020020.000", "DrivingReady": float("nan")},
        {"Date": "20261103020020.000", "DrivingReady": {}},
        {"Date": "20261103020020.000", "DrivingReady": "0"},
    ],
)
def test_malformed_observations_do_not_mutate_vehicle(observation):
    api, vehicle, helper = setup()
    helper.latest_state = observation
    before = copy.deepcopy(vehicle.__dict__)
    assert not api.apply_cached_mqtt_state(vehicle)
    assert vehicle.__dict__ == before
    api._request.assert_not_called()


def test_missing_ccs2_baseline_cannot_invent_a_state_snapshot():
    api, vehicle, helper = setup()
    vehicle.data = None
    helper.latest_state = delta()
    before = copy.deepcopy(vehicle.__dict__)
    assert not api.apply_cached_mqtt_state(vehicle)
    assert vehicle.__dict__ == before


def test_older_http_cache_cannot_roll_back_an_applied_unlock():
    api, vehicle, helper = setup()
    helper.latest_state = delta()
    assert api.apply_cached_mqtt_state(vehicle)
    api._request.side_effect = None
    api._request.return_value = {"resMsg": {"state": {"Vehicle": state()}}}
    api.update_vehicle_with_cached_state(
        Token(username="synthetic", password="synthetic"), vehicle
    )
    assert vehicle.is_locked is False
    assert vehicle.last_updated_at == dt.datetime(2026, 11, 3, 2, 0, 20, tzinfo=dt.UTC)
    assert api._request.call_count == 1


def test_equal_date_http_cache_cannot_overwrite_an_applied_unlock():
    api, vehicle, helper = setup()
    helper.latest_state = delta()
    assert api.apply_cached_mqtt_state(vehicle)
    api._request.side_effect = None
    api._request.return_value = {
        "resMsg": {"state": {"Vehicle": state("20261103020020.000")}}
    }
    api.update_vehicle_with_cached_state(
        Token(username="synthetic", password="synthetic"), vehicle
    )
    assert vehicle.is_locked is False
    assert api._request.call_count == 1


def test_recreated_vehicle_can_load_equal_date_http_baseline():
    api, vehicle, helper = setup()
    helper.latest_state = delta()
    assert api.apply_cached_mqtt_state(vehicle)
    replacement = Vehicle(id=vehicle.id, ccu_ccs2_protocol_support=1)
    api._request.side_effect = None
    api._request.return_value = {
        "resMsg": {"state": {"Vehicle": state("20261103020020.000")}}
    }
    api.update_vehicle_with_cached_location = Mock()
    api.update_vehicle_with_cached_state(
        Token(username="synthetic", password="synthetic"), replacement
    )
    assert replacement.data is not None
    assert replacement.is_locked is True
    assert api._request.call_count == 1


def test_mqtt_arriving_during_http_read_is_applied_before_candidate_commits():
    api, vehicle, helper = setup()

    def request(*args, **kwargs):
        helper.latest_state = delta()
        return {"resMsg": {"state": {"Vehicle": state("20261103020010.000")}}}

    api._request.side_effect = request
    api.update_vehicle_with_cached_location = Mock()
    api.update_vehicle_with_cached_state(
        Token(username="synthetic", password="synthetic"), vehicle
    )
    assert vehicle.is_locked is False
    assert vehicle.last_updated_at == dt.datetime(2026, 11, 3, 2, 0, 20, tzinfo=dt.UTC)
    assert api._request.call_count == 1


def test_diagnostics_are_counters_not_identifiers_or_payloads():
    api, vehicle, helper = setup()
    helper.latest_state = delta()
    assert api.apply_cached_mqtt_state(vehicle)
    summary = api.get_local_diagnostics()["passive_state"]
    assert summary == {
        "applied": 1,
        "stale": 0,
        "invalid": 0,
        "missing": 0,
        "last_result": "applied",
    }
    assert "synthetic" not in repr(summary)
    assert "Date" not in repr(summary)
