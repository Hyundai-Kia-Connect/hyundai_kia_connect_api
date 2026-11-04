"""Manager shutdown owns push cancellation and existing HTTP resources."""

from threading import Event, Thread
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import requests

from hyundai_kia_connect_api import VehicleManager
from hyundai_kia_connect_api.ApiImpl import ApiImpl
from hyundai_kia_connect_api.Vehicle import Vehicle


def manager():
    result = VehicleManager(
        region=9, brand=2, username="synthetic", password="synthetic", pin="0000"
    )
    result.api.close()
    result.api = Mock(spec=ApiImpl)
    result.vehicles = {"synthetic": Vehicle(id="synthetic")}
    return result


def test_close_stops_push_before_adapter_and_is_idempotent():
    fleet = manager()
    events = []
    fleet._push_session = SimpleNamespace(close=lambda: events.append("push"))
    fleet.api.close.side_effect = lambda: events.append("adapter")

    fleet.close()
    fleet.close()

    assert events == ["push", "adapter"]
    fleet.api.close.assert_called_once()
    assert not fleet.start_push("synthetic")


def test_adapter_cleanup_runs_even_when_push_stop_fails():
    fleet = manager()
    fleet._push_session = SimpleNamespace(close=Mock(side_effect=RuntimeError("stop")))

    with pytest.raises(RuntimeError, match="stop"):
        fleet.close()

    fleet.api.close.assert_called_once()
    assert not fleet.start_push("synthetic")


def test_close_can_reenter_through_a_push_callback():
    fleet = manager()
    fleet._push_session = SimpleNamespace(close=fleet.close)

    fleet.close()

    fleet.api.close.assert_called_once()


def test_close_during_startup_does_not_resurrect_push():
    fleet = manager()
    entering = Event()
    release = Event()
    stopped = Mock()

    def startup(_vehicle_id):
        entering.set()
        assert release.wait(2)
        return True

    fleet._push_session = SimpleNamespace(ensure_started=startup, close=stopped)
    result = []
    worker = Thread(target=lambda: result.append(fleet.start_push("synthetic")))
    worker.start()
    assert entering.wait(2)
    fleet.close()
    release.set()
    worker.join(2)

    assert not worker.is_alive()
    assert result == [False]
    assert stopped.call_count == 2
    assert fleet.api.close.call_count == 2


def test_adapter_closes_unique_owned_sessions_without_opening_lazy_resources():
    class LazyAdapter(ApiImpl):
        @property
        def sessions(self):
            raise AssertionError("shutdown must not create a session")

    api = LazyAdapter()
    primary = Mock(spec=requests.Session)
    service_hub = Mock(spec=requests.Session)
    api.session = primary
    api._service_hub_session = service_hub
    api._sessions = primary

    api.close()

    primary.close.assert_called_once()
    service_hub.close.assert_called_once()


def test_adapter_attempts_all_cleanup_even_if_one_session_fails():
    api = ApiImpl()
    api.session = Mock(spec=requests.Session)
    api._service_hub_session = Mock(spec=requests.Session)
    api._service_hub_session.close.side_effect = RuntimeError("close")

    with pytest.raises(RuntimeError, match="close"):
        api.close()

    api.session.close.assert_called_once()
    api._service_hub_session.close.assert_called_once()


def test_adapter_without_owned_sessions_can_close_repeatedly():
    api = ApiImpl()
    api.session = requests

    api.close()
    api.close()
